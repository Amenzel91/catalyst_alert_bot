#-*- coding: utf-8 -*-
# --- IMPORTS ---
import asyncio
import csv
import logging
import os
import platform
import re
import threading
import json
import io
from collections import deque, defaultdict
from datetime import datetime, time, timezone, timedelta

import discord
import feedparser
import numpy as np
import pandas as pd
import pytz
import requests
import tweepy
import yfinance as yf
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv
from langdetect import detect, lang_detect_exception
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from finviz.screener import Screener
import traceback

def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    log(f"--- GLOBAL EXCEPTION CAUGHT ---", "critical")
    log(f"Message: {msg}", "critical")
    exc = context.get('exception')
    if exc:
        traceback_details = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log(f"FULL TRACEBACK:\n{traceback_details}", "critical")

# --- COMPATIBILITY PATCH for pandas_ta ---
if not hasattr(np, "NaN"):
    np.NaN = np.nan
import pandas_ta as ta

# --- SETUP ---
load_dotenv()

# --- PATHING & CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# File Paths
WATCHLIST_FILE = os.path.join(SCRIPT_DIR, "watchlist.txt")
SEISMIC_WATCHLIST_FILE = os.path.join(SCRIPT_DIR, "seismic_watchlist.txt")
OPTIMIZED_PARAMS_FILE = os.path.join(SCRIPT_DIR, "optimized_parameters.json")
TWITTER_SOURCES_FILE = os.path.join(SCRIPT_DIR, "twitter_sources.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "catalyst_bot.log")
SEEN_LINKS_FILE = os.path.join(SCRIPT_DIR, "seen_links.txt")
REVERSAL_WATCHLIST_FILE = os.path.join(SCRIPT_DIR, "reversal_watchlist.txt")
LONGTERM_REVERSAL_WATCHLIST_FILE = os.path.join(SCRIPT_DIR, "longterm_reversal_watchlist.txt")
FEEDBACK_LOG_FILE = os.path.join(SCRIPT_DIR, "feedback_log.csv")
CATALYST_LOG_FILE = os.path.join(SCRIPT_DIR, "catalyst_log.jsonl")

# Discord & API Keys
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
FINVIZ_AUTH_TOKEN = os.getenv("FINVIZ_AUTH_TOKEN")

try:
    ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", 0))
    HEARTBEAT_CHANNEL_ID = int(os.getenv("HEARTBEAT_CHANNEL_ID", 0))
except (ValueError, TypeError):
    print("CRITICAL: Channel ID in .env file is not a valid integer.")
    exit()

# --- EXPANDED RSS FEEDS ---
RSS_SOURCES = {
    "PR Newswire": "https://www.prnewswire.com/rss/news-releases-list.rss",
    "Business Wire": "https://www.businesswire.com/portal/site/home/news/feed/",
    "GlobeNewswire": "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies",
    "ACCESSWIRE": "https://www.accesswire.com/rss/newsroom.ashx",
    "MarketWatch": "http://feeds.marketwatch.com/marketwatch/realtimeheadlines/"
}
SEC_RSS_FEEDS = {
    "Insider Buys (Form 4)": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=include&count=100&output=atom",
    "Institutional Stakes (13D)": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=13d&owner=include&count=100&output=atom",
    "Institutional Stakes (13G)": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=13g&owner=include&count=100&output=atom",
}

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE, 'a', 'utf-8'), logging.StreamHandler()])
def log(msg: str, level: str = "info"): getattr(logging, level, logging.info)(msg)

# --- SYSTEM THRESHOLDS & KEYWORDS ---
MAX_SHARE_PRICE = 10.00
TZ = pytz.timezone("America/Chicago")
EST = pytz.timezone("America/New_York") # For market-aware timing
VALID_US_EXCHANGES = {'NMS', 'NYQ', 'ASE', 'NASDAQ', 'NYSE', 'AMEX'}
CATALYST_SCORE_THRESHOLD = 18
SYMPATHY_SCAN_SCORE_THRESHOLD = 50
COOLDOWN_PERIOD_MINUTES = 30
SEISMIC_VOLUME_MULTIPLIER = 10.0
SEISMIC_WATCHLIST_MAX_SIZE = 250
GAP_PERCENT_THRESHOLD = 10.0
GAP_VOLUME_THRESHOLD = 10000
VADER_FLUFF_THRESHOLD = 75
ANOMALY_GAIN_THRESHOLD_PCT = 20.0

# --- FINVIZ SCREENER DEFINITION ---
FINVIZ_SCREENER_PARAMS = [
    'cap_micro', 'p_price_u10', 'sh_float_u20', 'sh_avgvol_o100',
    'sh_relvol_o2', 'perf_todayup10', 'geo_usa',
]

# --- Technical & Pattern Thresholds ---
BREAKOUT_VOLUME_MULTIPLIER = 3.0
BREAKOUT_PRICE_CHANGE_PERCENT = 5.0

# --- Keywords from analyzer results ---
HIGH_IMPACT_POSITIVE = {
    "fda approval": 50, "breakthrough": 40, "partnership": 30, "agreement": 30,
    "acquisition": 35, "acquire": 35, "positive results": 35, "confirms": 40,
    "regain compliance": 25, "met all key endpoints": 50, "statistically significant": 40,
    "topline results": 35, "unveils": 20, "biological efficacy": 40,
    "efficacy demonstrated": 40, "letter of intent": 35, "business combination": 30,
    "strong financial results": 25, "raised": 20, "valuation": 20
}
HIGH_IMPACT_NEGATIVE = {"offering": -60, "dilution": -60, "investigation": -50, "halts trial": -60, "delisting": -80, "reverse split": -50}
LOW_IMPACT_SOURCES = {'Stocknews.com'}

# --- GLOBAL STATE ---
ANALYZER = SentimentIntensityAnalyzer()
FEEDBACK_EMOJIS = ['👍', '👎', '💰']
feedback_log_lock = threading.Lock()

# --- HELPER & ANALYSIS FUNCTIONS ---
def log_feedback(timestamp, user_id, ticker, alert_type, feedback):
    with feedback_log_lock:
        file_exists = os.path.isfile(FEEDBACK_LOG_FILE)
        with open(FEEDBACK_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['timestamp', 'user_id', 'ticker', 'alert_type', 'feedback'])
            writer.writerow([timestamp, user_id, ticker, alert_type, feedback])

def get_blocking_info(ticker: str):
    return yf.Ticker(ticker).info

def get_webull_url(ticker: str, info: dict) -> str:
    exchange_map = {'NMS': 'nasdaq', 'NYQ': 'nyse', 'ASE': 'amex'}
    exchange = info.get('exchange')
    wb_exchange = exchange_map.get(exchange, 'nasdaq')
    return f"https://www.webull.com/quote/{wb_exchange}-{ticker.lower()}"

def calculate_catalyst_score(text: str) -> int:
    text_lower = text.lower()
    vader_score = ANALYZER.polarity_scores(text)['compound'] * 100
    all_keywords = {**HIGH_IMPACT_POSITIVE, **HIGH_IMPACT_NEGATIVE}
    keyword_hits = {k: v for k, v in all_keywords.items() if k in text_lower}
    final_score = vader_score
    for score in keyword_hits.values(): final_score += score
    if not keyword_hits and vader_score < VADER_FLUFF_THRESHOLD: return 0
    return int(final_score)

def parse_sec_filing_title(title: str) -> tuple[str | None, str | None]:
    ticker_match = re.search(r'\s\(([A-Z]{1,5})\)', title)
    ticker = ticker_match.group(1) if ticker_match else None
    name_match = re.search(r'-\s(.*?)\s\(', title)
    company_name = name_match.group(1).strip() if name_match else title
    return company_name, ticker

def log_catalyst_to_db(catalyst_data: dict):
    try:
        with open(CATALYST_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(catalyst_data) + '\n')
    except Exception as e:
         log(f"Error writing to catalyst log: {e}", "error")

def yahoo_search_fallback(name: str) -> str:
    try:
        query = name.replace(" ", "+")
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
        headers = {'User-Agent': USER_AGENT}
        
        r = requests.get(url, headers=headers, timeout=3)
        
        if r.status_code == 404:
            log(f"FALLBACK SEARCH: 404 Not Found for query '{query}'. URL: {url}", "warning")
            return None

        r.raise_for_status()
        
        data = r.json()
        for quote in data.get('quotes', []):
            if quote.get('quoteType') == 'EQUITY':
                return quote['symbol']
                
    except requests.RequestException as e:
        log(f"FALLBACK SEARCH: Network error for query '{name}': {e}", "error")
        return None
        
    return None

def lookup_ticker(title: str) -> str:
    potential_tickers = re.findall(r"\(([A-Z]{1,5})\)", title)
    if potential_tickers:
        for ticker in potential_tickers:
            try:
                info = yf.Ticker(ticker).info
                if info and info.get('quoteType') is not None:
                    company_name = info.get('shortName', '').lower()
                    if company_name and company_name.split()[0] in title.lower():
                        return ticker.upper()
            except Exception as e:
                log(f"LOOKUP_TICKER: yfinance check for '{ticker}' failed. It may be delisted or invalid. Error: {e}", "warning")
                continue
                
    ticker_match_start = re.search(r"^([A-Z]{1,5}):", title)
    if ticker_match_start:
        return ticker_match_start.group(1).upper()
        
    name_match = re.match(r"^([\w\s.\'-]+?)\s*(?:reports|announces|unveils|signs|enters)", title, re.IGNORECASE)
    if name_match:
        name = name_match.group(1).strip()
        return yahoo_search_fallback(name)
        
    return None

def read_watchlist_file(filepath):
    try:
        with open(filepath, 'r') as f: return {line.strip() for line in f if line.strip()}
    except FileNotFoundError: return set()

def detect_bull_flag(df: pd.DataFrame, params: dict) -> bool:
    if len(df) < params['POLE_LOOKBACK_DAYS'] + params['FLAG_MIN_DURATION']: return False
    lookback_window = df.iloc[-(params['POLE_LOOKBACK_DAYS'] + params['FLAG_MAX_DURATION']):]
    if lookback_window.empty: return False
    relative_pole_top_index = np.argmax(lookback_window['High'].values)
    pole_top_price = lookback_window['High'].iloc[relative_pole_top_index]
    try:
        pole_top_date = lookback_window.index[relative_pole_top_index]
        pole_top_index = df.index.get_loc(pole_top_date)
    except (KeyError, IndexError): return False
    pole_df = df.iloc[max(0, pole_top_index - params['POLE_LOOKBACK_DAYS']):pole_top_index]
    if pole_df.empty: return False
    pole_bottom_price = pole_df['Low'].min()
    if isinstance(pole_bottom_price, pd.Series): pole_bottom_price = pole_bottom_price.iloc[0]
    if isinstance(pole_top_price, pd.Series): pole_top_price = pole_top_price.iloc[0]
    if pole_bottom_price == 0: return False
    pole_increase = ((pole_top_price - pole_bottom_price) / pole_bottom_price) * 100
    if isinstance(pole_increase, pd.Series): pole_increase = pole_increase.iloc[0]
    if pole_increase < params['POLE_MIN_INCREASE_PCT']: return False
    flag_df = df.iloc[pole_top_index + 1:]
    if not (params['FLAG_MIN_DURATION'] <= len(flag_df) <= params['FLAG_MAX_DURATION']): return False
    flag_low_price = flag_df['Low'].min()
    if isinstance(flag_low_price, pd.Series): flag_low_price = flag_low_price.iloc[0]
    pole_height = pole_top_price - pole_bottom_price
    if pole_height <= 0: return False
    pullback_amount = pole_top_price - flag_low_price
    if (pullback_amount / pole_height) > params['FLAG_MAX_PULLBACK_PCT']: return False
    flag_highs = flag_df['High']
    x_indices = np.arange(len(flag_highs))
    try:
        high_coeffs = np.polyfit(x_indices, flag_highs, 1)
        high_slope = high_coeffs[0]
    except (np.linalg.LinAlgError, TypeError): return False
    if high_slope > 0: return False
    upper_trendline_price_now = high_slope * (len(x_indices) - 1) + high_coeffs[1]
    last_close_price = df['Close'].iloc[-1]
    if isinstance(last_close_price, pd.Series): last_close_price = last_close_price.iloc[0]
    if last_close_price > upper_trendline_price_now: return True
    return False

def detect_ascending_triangle(df: pd.DataFrame, params: dict) -> bool:
    df_slice = df.iloc[-params['LOOKBACK_DAYS']:]
    if len(df_slice) < 20: return False
    resistance_level = df_slice['High'].max()
    if isinstance(resistance_level, pd.Series): resistance_level = resistance_level.iloc[0]
    resistance_touches = df_slice[abs(df_slice['High'] - resistance_level) / resistance_level * 100 < params['RESISTANCE_TOLERANCE_PCT']]
    if len(resistance_touches) < params['MIN_TOUCHES']: return False
    first_touch_date = resistance_touches.index[0]
    support_df = df_slice[df_slice.index > first_touch_date]
    if len(support_df) < params['MIN_TOUCHES'] * 2: return False
    lows = support_df['Low']
    swing_lows = lows[(lows.shift(1) > lows) & (lows.shift(-1) > lows)]
    if len(swing_lows) < params['MIN_TOUCHES']: return False
    if not all(swing_lows.diff().dropna() > 0): return False
    last_close = df_slice['Close'].iloc[-1]
    if isinstance(last_close, pd.Series): last_close = last_close.iloc[0]
    if last_close > resistance_level: return True
    return False

def detect_pennant(df: pd.DataFrame, params: dict) -> bool:
    if len(df) < params['POLE_LOOKBACK_DAYS'] + params['PENNANT_MIN_DURATION']: return False
    lookback_window = df.iloc[-(params['POLE_LOOKBACK_DAYS'] + params['PENNANT_MAX_DURATION']):]
    if lookback_window.empty: return False
    relative_pole_top_index = np.argmax(lookback_window['High'].values)
    pole_top_price = lookback_window['High'].iloc[relative_pole_top_index]
    try:
        pole_top_date = lookback_window.index[relative_pole_top_index]
        pole_top_index = df.index.get_loc(pole_top_date)
    except (KeyError, IndexError): return False
    pole_df = df.iloc[max(0, pole_top_index - params['POLE_LOOKBACK_DAYS']):pole_top_index]
    if pole_df.empty: return False
    pole_bottom_price = pole_df['Low'].min()
    if isinstance(pole_bottom_price, pd.Series): pole_bottom_price = pole_bottom_price.iloc[0]
    if isinstance(pole_top_price, pd.Series): pole_top_price = pole_top_price.iloc[0]
    if pole_bottom_price == 0: return False
    pole_increase = ((pole_top_price - pole_bottom_price) / pole_bottom_price) * 100
    if isinstance(pole_increase, pd.Series): pole_increase = pole_increase.iloc[0]
    if pole_increase < params['POLE_MIN_INCREASE_PCT']: return False
    pennant_df = df.iloc[pole_top_index + 1:]
    if not (params['PENNANT_MIN_DURATION'] <= len(pennant_df) <= params['PENNANT_MAX_DURATION']): return False
    highs = pennant_df['High']; lows = pennant_df['Low']
    x = np.arange(len(highs))
    try:
        high_coeffs = np.polyfit(x, highs, 1)
        low_coeffs = np.polyfit(x, lows, 1)
    except (np.linalg.LinAlgError, TypeError): return False
    high_slope, low_slope = high_coeffs[0], low_coeffs[0]
    if high_slope >= 0 or low_slope <= 0: return False
    upper_trendline_now = high_slope * (len(x) - 1) + high_coeffs[1]
    last_close = df['Close'].iloc[-1]
    if isinstance(last_close, pd.Series): last_close = last_close.iloc[0]
    if last_close > upper_trendline_now: return True
    return False

class WhisperStream(tweepy.StreamingClient):
    def __init__(self, bearer_token, bot_instance):
        super().__init__(bearer_token)
        self.bot = bot_instance
        self.loop = asyncio.get_event_loop()

    def on_connect(self): log("Twitter Whisper Stream connected successfully.")
    def on_tweet(self, tweet):
        log(f"TWITTER HIT: @{tweet.author_id}: '{tweet.text[:60].replace('\n',' ')}...'")
        tickers_found = re.findall(r"\$([A-Za-z]{1,5})", tweet.text)
        if tickers_found:
            for ticker in tickers_found:
                headline = f"Twitter mention by @{tweet.author_id}"
                link = f"https://twitter.com/{tweet.author_id}/status/{tweet.id}"
                asyncio.run_coroutine_threadsafe(self.bot.update_seismic_watchlist(ticker.upper(), headline, link), self.loop)

    def on_error(self, status): log(f"Twitter stream error: {status}", "error")

def start_twitter_stream(bot_instance):
    if not X_BEARER_TOKEN: log("X_BEARER_TOKEN not found, Twitter stream will not start.", "warning"); return
    try:
        with open(TWITTER_SOURCES_FILE, 'r') as f: author_ids = [line.strip() for line in f if line.strip()]
        if not author_ids: log("twitter_sources.txt is empty, stream will not start.", "warning"); return
        stream = WhisperStream(X_BEARER_TOKEN, bot_instance)
        rules = stream.get_rules().data
        if rules: stream.delete_rules([rule.id for rule in rules])
        rule_query = f"from:{' OR from:'.join(author_ids)} -is:retweet -is:reply"
        stream.add_rules(tweepy.StreamRule(rule_query)); log(f"Twitter stream rule set: '{rule_query}'")
        stream.filter(expansions=["author_id"])
    except FileNotFoundError: log(f"{TWITTER_SOURCES_FILE} not found, Twitter stream cannot start.", "warning")
    except Exception as e: log(f"Fatal error starting Twitter stream: {e}", "critical")

class ConfirmClearView(discord.ui.View):
    def __init__(self): super().__init__(timeout=30); self.value = None
    @discord.ui.button(label='Confirm Clear', style=discord.ButtonStyle.danger)
    async def confirm(self, i: discord.Interaction, b: discord.ui.Button): self.value = True; self.stop(); await i.response.edit_message(content='Watchlist cleared.', view=None)
    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.grey)
    async def cancel(self, i: discord.Interaction, b: discord.ui.Button): self.value = False; self.stop(); await i.response.edit_message(content='Operation cancelled.', view=None)

class CatalystBot(discord.Client):
    def __init__(self, **kwargs):
        intents = discord.Intents.default()
        intents.reactions = True
        super().__init__(intents=intents, **kwargs)
        
        self.tree = app_commands.CommandTree(self)
        self.gappers_scan_done_today = False
        self.investigation_watchlist = set()
        self.recently_alerted_tickers = {}
        self.anomaly_investigated_tickers = set()
        self.finviz_scanner_failing = False
        self.last_finviz_screener_results = set()
        self.seismic_penalty_box = defaultdict(int)
        self.SEISMIC_PENALTY_THRESHOLD = 5 # Ignore a ticker after 5 consecutive failures
        self.squeeze_alerted_today = set()
        self.pattern_alerted_today = set()
        self.seen_links = set()
        self.optimized_params = {}
        self.heartbeat_alerts = []
        self.heartbeat_articles_processed = 0
        self.heartbeat_seismic_hits = set()
        self.heartbeat_squeezes = set()
        self.heartbeat_anomalies = set()
        self.confirmation_watchlist = {}
        self.continuation_watchlist = {}
        self.continuation_checkpoints = {}
        self.daily_seismic_additions = defaultdict(list)

    async def setup_hook(self):
        # Attach the global "black box" recorder to the bot's event loop
        self.loop.set_exception_handler(handle_exception)
        
        try:
            with open(SEEN_LINKS_FILE, "r") as f: self.seen_links = set(line.strip() for line in f)
        except FileNotFoundError: self.seen_links = set()
        watchlist_group.parent = self.tree; await self.tree.sync(); log("Command tree synced.")

    async def on_ready(self):
        log(f'Logged in as {self.user.name}'); self.alert_channel = self.get_channel(ALERT_CHANNEL_ID); self.heartbeat_channel = self.get_channel(HEARTBEAT_CHANNEL_ID)
        if not self.alert_channel or not self.heartbeat_channel: log("CRITICAL: Channel ID not found.", "critical"); await self.close(); return
        
        self.finviz_news_scanner_loop.start()
        self.breakout_confirmation_loop.start()
        self.continuation_momentum_loop.start()
        self.seismic_digest_loop.start()
        self.news_api_scanner_loop.start()
        self.sec_filings_scanner_loop.start()
        self.pre_market_anomaly_scan_loop.start()
        self.finviz_screener_scan_loop.start()
        self.seismic_scanner_loop.start()
        self.daily_reset_loop.start()
        self.heartbeat_loop.start()
        self.technical_breakout_scanner_loop.start()
        self.advanced_pattern_scanner_loop.start()
        log("All scanner loops started.")
        
    async def update_seismic_watchlist(self, ticker: str, headline: str, link: str):
        try:
            info = await asyncio.to_thread(get_blocking_info, ticker)
            price = info.get('currentPrice', info.get('regularMarketPrice'))
            if price is None or price > MAX_SHARE_PRICE: return

            try:
                with open(SEISMIC_WATCHLIST_FILE, 'r') as f: tickers = {line.strip() for line in f if line.strip()}
            except FileNotFoundError: tickers = set()

            if ticker not in tickers:
                tickers.add(ticker)
                ticker_list = list(tickers)
                if len(ticker_list) > SEISMIC_WATCHLIST_MAX_SIZE: ticker_list = ticker_list[-SEISMIC_WATCHLIST_MAX_SIZE:]
                with open(SEISMIC_WATCHLIST_FILE, 'w') as f:
                    for t in sorted(ticker_list): f.write(t + '\n')
                log(f"Updated seismic_watchlist.txt with ${ticker}. New size: {len(tickers)}")
                self.daily_seismic_additions[ticker].append(f"[{headline}]({link})")
        except Exception as e: log(f"Error updating seismic watchlist for ${ticker}: {e}", "error")

    async def find_sympathy_plays(self, ticker: str):
        if not FINNHUB_API_KEY: log("Cannot run sympathy scan; FINNHUB_API_KEY is not set.", "warning"); return
        log(f"SYMPATHY SCAN: Searching for peers of ${ticker}...")
        try:
            url = f"https://finnhub.io/api/v1/stock/peers?symbol={ticker}&token={FINNHUB_API_KEY}"
            r = await asyncio.to_thread(requests.get, url, timeout=5)
            r.raise_for_status()
            peers = r.json()
            if not peers or len(peers) <= 1: return
            if peers[0] == ticker: peers = peers[1:]
            log(f"SYMPATHY SCAN: Found {len(peers)} peers for ${ticker}. Adding to seismic: {', '.join(peers)}")
            headline = f"Sympathy play for ${ticker}"
            for p in peers:
                if p:
                    await self.update_seismic_watchlist(p, headline, "N/A")
        except Exception as e: log(f"SYMPATHY SCAN: Error for ${ticker}: {e}", "error")

    async def get_trading_data_snapshot(self, ticker: str) -> dict:
        snapshot = {"Float": "N/A", "Short Float": "N/A", "RVOL": "N/A", "VWAP": "N/A"}
        try:
            data = await asyncio.to_thread(yf.Ticker(ticker).info)
            if data.get('sharesFloat'):
                snapshot['Float'] = f"{data['sharesFloat'] / 1_000_000:.2f}M"
            if data.get('shortPercentOfFloat'):
                snapshot['Short Float'] = f"{data['shortPercentOfFloat'] * 100:.2f}%"
            
            hist = await asyncio.to_thread(yf.download, ticker, period="1d", interval="1m", progress=False)
            if not hist.empty:
                hist.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}, inplace=True)
                vwap = ta.vwap(high=hist['high'], low=hist['low'], close=hist['close'], volume=hist['volume'])
                if vwap is not None and not vwap.empty:
                    snapshot['VWAP'] = f"${vwap.iloc[-1]:.2f}"
        except Exception as e:
            log(f"Could not calculate VWAP/Float for ${ticker}: {e}", "warning")
        return snapshot

    async def send_alert_and_get_feedback(self, embed, ticker: str):
        try:
            snapshot = await self.get_trading_data_snapshot(ticker)
            embed.add_field(name="Float", value=snapshot["Float"], inline=True)
            embed.add_field(name="Short Float", value=snapshot["Short Float"], inline=True)
            embed.add_field(name="VWAP", value=snapshot["VWAP"], inline=True)
            chart_url = f"https://finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=i5&s=l"
            embed.set_image(url=chart_url)
            message = await self.alert_channel.send(embed=embed)
            for emoji in FEEDBACK_EMOJIS:
                await message.add_reaction(emoji)
        except Exception as e:
            log(f"Error sending alert or adding reactions for ${ticker}: {e}", "error")

    async def process_news_item(self, title, link, summary, source, ticker=None):
        self.heartbeat_articles_processed += 1
        if not link or link in self.seen_links: return
        
        try:
            if detect(title) != 'en': return
        except lang_detect_exception.LangDetectException: pass

        if ticker is None: 
            ticker = await asyncio.to_thread(lookup_ticker, title)
        
        if not ticker: 
            return
        
        if '.' in ticker and not (ticker.endswith('.A') or ticker.endswith('.B')):
            log(f"FILTERED: Rejecting international/suffixed ticker ${ticker}.")
            return
    
        try:
            info = await asyncio.to_thread(get_blocking_info, ticker)
            
            if not info or not isinstance(info, dict):
                log(f"YFINANCE LOOKUP FAILED for ${ticker}. No info dictionary returned.", "warning")
                return

            if info.get('quoteType') is None:
                log(f"YFINANCE LOOKUP FAILED for ${ticker}. Ticker may be invalid or delisted. News: '{title[:60]}...'", "warning")
                return

            exchange = info.get('exchange')
            if exchange not in VALID_US_EXCHANGES: 
                return
                
            price = info.get('currentPrice', info.get('regularMarketPrice'))
            if price is None or price > MAX_SHARE_PRICE:
                if source != 'Finviz News': 
                    return
            
            log_catalyst_to_db({
                "capture_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "ticker": ticker, "headline": title,
                "summary": summary, "source": source, "link": link,
            })
            self.seen_links.add(link)
            with open(SEEN_LINKS_FILE, 'a') as f: f.write(link + '\n')
            
        except Exception as e:
            log(f"YFINANCE ERROR for ${ticker}: Could not get info for news item '{title[:60]}...'. Error: {e}", "warning")
            return

        original_score = calculate_catalyst_score(f"{title}. {summary}")
        score = float(original_score)
        
        if source in LOW_IMPACT_SOURCES:
            score *= 0.5

        current_time_est = datetime.now(EST)
        current_hour_est = current_time_est.hour
        
        if 7 <= current_hour_est < 9:
            score *= 1.15
        elif 4 <= current_hour_est < 7:
            score *= 0.80
        
        final_score = int(score)
        log(f"DIAGNOSTIC: Final score for ${ticker} ('{title[:40]}...'): {final_score} (Original: {original_score})")

        if ticker in self.recently_alerted_tickers and (datetime.now(timezone.utc) - self.recently_alerted_tickers[ticker]).total_seconds() < COOLDOWN_PERIOD_MINUTES * 60: return

        if final_score >= CATALYST_SCORE_THRESHOLD:
            self.heartbeat_alerts.append(f"📈 High-Impact News: ${ticker}")
            self.recently_alerted_tickers[ticker] = datetime.now(timezone.utc)
            self.confirmation_watchlist[ticker] = {'timestamp': datetime.now(timezone.utc), 'headline': title, 'link': link}
            log(f"High-impact news for ${ticker} (Score: {final_score}). Added to confirmation watchlist.")
            embed = discord.Embed(title=f"📈 High-Impact News: ${ticker}", color=0xFFA500, url=get_webull_url(ticker, info))
            embed.description = f"[{title}]({link})"
            if price:
                embed.add_field(name="Price", value=f"${price:,.2f}", inline=True)
            embed.add_field(name="Score", value=str(final_score), inline=True)
            embed.set_footer(text="Potential catalyst detected. Monitoring for breakout confirmation. | NFA")
            await self.send_alert_and_get_feedback(embed, ticker)
            if final_score >= SYMPATHY_SCAN_SCORE_THRESHOLD:
                asyncio.create_task(self.find_sympathy_plays(ticker))
        else:
            await self.update_seismic_watchlist(ticker, title, link)

    @tasks.loop(seconds=30.0)
    async def finviz_news_scanner_loop(self):
        log("--- Starting Finviz News Scan ---")
        if not FINVIZ_AUTH_TOKEN:
            log("FINVIZ_AUTH_TOKEN not found in .env file. Finviz news scanner will not run.", "warning")
            await asyncio.sleep(3600)
            return
        
        url = ""
        try:
            base_url = "https://elite.finviz.com/news_export.ashx"
            filters = "f=sh_price_u10"
            url = f"{base_url}?{filters}&auth={FINVIZ_AUTH_TOKEN}"
            headers = {'User-Agent': USER_AGENT}
            
            response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                log(f"FINVIZ NEWS SCAN HTTP ERROR: Status {response.status_code} for URL: {url}", "error")
                log(f"Response text (first 200 chars): {response.text[:200]}", "error")
                log("--- Finviz News Scan Finished (with errors) ---")
                return

            csv_file = io.StringIO(response.text)
            reader = csv.DictReader(csv_file)
            if not reader.fieldnames:
                log("Finviz news feed is empty or returned non-CSV data. Check auth token.", "warning")
                log("--- Finviz News Scan Finished (with empty data) ---")
                return
                
            for row in reader:
                link = row.get('URL') or row.get('Link')
                title = row.get('Title')
                if link and title and link not in self.seen_links:
                    await self.process_news_item(title=title, link=link, summary='', source='Finviz News')

        except requests.RequestException as e:
            log(f"FINVIZ NEWS SCAN NETWORK ERROR: Could not connect to {url}. Details: {e}", "error")
        except Exception as e:
            log(f"An unexpected error occurred in Finviz news scan: {e}", "error")
            
        log("--- Finviz News Scan Finished ---")
        
    @tasks.loop(minutes=30.0)
    async def heartbeat_loop(self):
        embed = discord.Embed(title="❤️ Bot Situation Report", color=0x2ECC71, timestamp=datetime.now(timezone.utc))
        alerts_str = "\n".join(self.heartbeat_alerts) if self.heartbeat_alerts else "None"
        embed.add_field(name="Alerts Sent (Last 30 Min)", value=alerts_str, inline=False)
        embed.add_field(name="Articles Processed", value=str(self.heartbeat_articles_processed), inline=True)
        anomaly_str = ", ".join(f"${t}" for t in self.heartbeat_anomalies) if self.heartbeat_anomalies else "None"
        embed.add_field(name="Anomalies Investigated", value=anomaly_str, inline=True)
        seismic_str = ", ".join(f"${t}" for t in self.heartbeat_seismic_hits) if self.heartbeat_seismic_hits else "None"
        embed.add_field(name="New Seismic Hits", value=seismic_str, inline=True)
        await self.heartbeat_channel.send(embed=embed)
        self.heartbeat_alerts.clear(); self.heartbeat_articles_processed = 0; self.heartbeat_seismic_hits.clear(); self.heartbeat_anomalies.clear()

    @tasks.loop(minutes=15.0)
    async def news_api_scanner_loop(self):
        if not NEWS_API_KEY:
            return 
        log("--- Starting News API Scan (Primary Redundancy) ---")
        try:
            url = (f"https://newsapi.org/v2/top-headlines?"
                   f"country=us&"
                   f"category=business&"
                   f"apiKey={NEWS_API_KEY}")
            response = await asyncio.to_thread(requests.get, url, headers={'User-Agent': USER_AGENT}, timeout=15)
            response.raise_for_status()
            data = response.json()
            articles = data.get('articles', [])
            log(f"News API scan found {len(articles)} articles.")
            for article in articles:
                title = article.get('title', '')
                link = article.get('url', '')
                summary = article.get('description', '')
                source = article.get('source', {}).get('name', 'NewsAPI')
                await self.process_news_item(title, link, summary, source)
        except requests.RequestException as e:
            log(f"Network error processing News API: {e}", "error")
        except Exception as e:
            log(f"An unexpected error occurred in News API scanner: {e}", "error")
        log("--- News API Scan Finished ---")

    @tasks.loop(minutes=10)
    async def sec_filings_scanner_loop(self):
        log("--- Starting SEC Filings Scan (via Finviz) ---")
        if not FINVIZ_AUTH_TOKEN:
            log("FINVIZ_AUTH_TOKEN not found in .env file. SEC scanner will not run.", "warning")
            return
        
        scan_list = list(read_watchlist_file(WATCHLIST_FILE).union(read_watchlist_file(SEISMIC_WATCHLIST_FILE)))
        if not scan_list:
            log("--- SEC Filings Scan Finished (No tickers to scan) ---")
            return
        
        try:
            for i in range(0, len(scan_list), 100):
                chunk = scan_list[i:i+100]
                ticker_string = ",".join(chunk)
                base_url = "https://elite.finviz.com/export.ashx"
                params = f"v=152&t={ticker_string}&auth={FINVIZ_AUTH_TOKEN}"
                url = f"{base_url}?{params}"
                headers = {'User-Agent': USER_AGENT}
                
                response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=20)
                
                if response.status_code != 200:
                    log(f"SEC SCAN HTTP ERROR: Status {response.status_code} for URL chunk starting with {chunk[0]}...", "error")
                    log(f"Response text (first 200 chars): {response.text[:200]}", "error")
                    continue

                csv_file = io.StringIO(response.text)
                reader = csv.DictReader(csv_file)
                for row in reader:
                    ticker = row.get('Ticker')
                    headline = f"SEC Filing ({row.get('Last Filing')}) for ${ticker}"
                    unique_id = f"{ticker}_{row.get('Last Filing')}_{row.get('Date')}"
                    if unique_id not in self.seen_links:
                        await self.process_news_item(
                            title=headline,
                            link=unique_id,
                            summary=f"Insider: {row.get('Insider Trading')}",
                            source='SEC (via Finviz)',
                            ticker=ticker
                        )
        except requests.RequestException as e:
            log(f"SEC SCAN NETWORK ERROR: Could not connect to Finviz. Details: {e}", "error")
        except Exception as e:
            log(f"An unexpected error occurred in SEC filings scan: {e}", "error")
            
        log("--- SEC Filings Scan Finished ---")
        
    @tasks.loop(seconds=20.0)
    async def breakout_confirmation_loop(self):
        if not self.confirmation_watchlist: return
        now = datetime.now(timezone.utc)
        for ticker, data in list(self.confirmation_watchlist.items()):
            alert_time = data['timestamp']
            if now - alert_time > timedelta(minutes=20):
                log(f"CONFIRMATION TIMEOUT: ${ticker} did not break out. Moving to seismic watchlist.")
                headline = f"No breakout after alert: '{data['headline']}'"
                await self.update_seismic_watchlist(ticker, headline, data['link'])
                del self.confirmation_watchlist[ticker]
                continue
            try:
                hist = await asyncio.to_thread(yf.download, ticker, period="1d", interval="1m", progress=False)
                if hist.empty or len(hist) < 5: continue
                price_now = hist['Close'].iloc[-1]
                price_then = hist['Close'].iloc[-3]
                if price_then == 0: continue
                price_change = ((price_now - price_then) / price_then) * 100
                volume_now = hist['Volume'].iloc[-1]
                avg_volume = hist['Volume'].iloc[-5:-1].mean()
                if price_change > BREAKOUT_PRICE_CHANGE_PERCENT and volume_now > (avg_volume * BREAKOUT_VOLUME_MULTIPLIER) and volume_now > 1000:
                    log(f"✅ BREAKOUT CONFIRMED: ${ticker} | Price Change: +{price_change:.2f}%")
                    self.continuation_watchlist[ticker] = now
                    self.continuation_checkpoints[ticker] = []
                    del self.confirmation_watchlist[ticker]
                    info = await asyncio.to_thread(get_blocking_info, ticker)
                    embed = discord.Embed(title=f"✅ Breakout Confirmed: ${ticker}", color=0x2ECC71, url=get_webull_url(ticker, info))
                    embed.add_field(name="Price", value=f"${price_now:,.2f}", inline=True).add_field(name="Breakout", value=f"+{price_change:.2f}% in 2 min", inline=True)
                    await self.send_alert_and_get_feedback(embed, ticker)
            except Exception as e:
                log(f"Error in breakout confirmation for ${ticker}: {e}", "warning")
                del self.confirmation_watchlist[ticker]

    @tasks.loop(minutes=5.0)
    async def continuation_momentum_loop(self):
        if not self.continuation_watchlist: return
        now = datetime.now(timezone.utc)
        checkpoints = [10, 20, 30, 60]
        for ticker, breakout_time in list(self.continuation_watchlist.items()):
            minutes_since_breakout = (now - breakout_time).total_seconds() / 60
            if minutes_since_breakout > 65:
                del self.continuation_watchlist[ticker]
                if ticker in self.continuation_checkpoints: del self.continuation_checkpoints[ticker]
                continue
            try:
                announced_cps = self.continuation_checkpoints.get(ticker, [])
                for cp in checkpoints:
                    if minutes_since_breakout >= cp and cp not in announced_cps:
                        snapshot = await self.get_trading_data_snapshot(ticker)
                        vwap_str = snapshot.get("VWAP", "$0.00").replace('$', '')
                        vwap = float(vwap_str)
                        info = await asyncio.to_thread(get_blocking_info, ticker)
                        price_now = info.get('currentPrice', info.get('regularMarketPrice', 0))
                        if price_now > vwap:
                            embed = discord.Embed(title=f"📈 Continuation: ${ticker}", color=0x3498DB, description=f"After **{cp} minutes**, ${ticker} is holding strong above its VWAP of ${vwap:.2f}.")
                            await self.alert_channel.send(embed=embed)
                        else:
                            embed = discord.Embed(title=f"📉 Momentum Fading: ${ticker}", color=0xE74C3C, description=f"After **{cp} minutes**, ${ticker} has fallen below its VWAP of ${vwap:.2f}.")
                            await self.alert_channel.send(embed=embed)
                            del self.continuation_watchlist[ticker]
                            if ticker in self.continuation_checkpoints: del self.continuation_checkpoints[ticker]
                        self.continuation_checkpoints.setdefault(ticker, []).append(cp)
                        break
            except Exception as e:
                log(f"Error in continuation check for ${ticker}: {e}", "warning")
                del self.continuation_watchlist[ticker]
                if ticker in self.continuation_checkpoints: del self.continuation_checkpoints[ticker]
        
    @tasks.loop(time=time(19, 0, tzinfo=TZ))
    async def seismic_digest_loop(self):
        log("--- Generating Daily Seismic Digest ---")
        if not self.daily_seismic_additions:
            log("No new additions to the seismic watchlist today.")
            return

        embed = discord.Embed(
            title=f"Seismic Watchlist Digest: {datetime.now(TZ).strftime('%Y-%m-%d')}",
            description="Summary of all tickers added to the seismic watchlist today for extended monitoring.",
            color=0x7289DA
        )
        for ticker, stories in self.daily_seismic_additions.items():
            value = "\n".join(stories)
            if len(value) > 1024:
                value = value[:1020] + "..."
            embed.add_field(name=f"${ticker}", value=value, inline=False)
        await self.alert_channel.send(embed=embed)
        self.daily_seismic_additions.clear()

    @tasks.loop(minutes=5.0)
    async def pre_market_anomaly_scan_loop(self):
        log("--- Starting Pre-Market Anomaly Scan ---")
        if not (time(3, 0) <= datetime.now(TZ).time() <= time(8, 30)):
            return

        url = f"https://www.alphavantage.co/query?function=TOP_GAINERS_LOSERS&apikey={ALPHA_VANTAGE_API_KEY}"
        
        try:
            r = await asyncio.to_thread(requests.get, url, timeout=15)
            
            if r.status_code != 200:
                log(f"ANOMALY SCAN HTTP ERROR: Status {r.status_code} for URL: {url}", "error")
                log(f"Response text (first 200 chars): {r.text[:200]}", "error")
                return

            data = r.json()
            
            if "Note" in data or "Error Message" in data:
                log(f"ANOMALY SCAN API ERROR: {data.get('Note', data.get('Error Message'))}", "error")
                return

            for gainer in data.get('top_gainers', []):
                ticker = gainer['ticker']
                if float(gainer['price']) < MAX_SHARE_PRICE and float(gainer['change_percentage'].rstrip('%')) > ANOMALY_GAIN_THRESHOLD_PCT:
                    if ticker not in self.anomaly_investigated_tickers:
                        self.anomaly_investigated_tickers.add(ticker)
                        log(f"ANOMALY DETECTED: {ticker}")

        except requests.RequestException as e:
            log(f"ANOMALY SCAN NETWORK ERROR: Could not connect to {url}. Details: {e}", "error")
        except Exception as e:
            log(f"ANOMALY SCAN: An unexpected error occurred: {e}", "error")

    @tasks.loop(minutes=5.0)
    async def finviz_screener_scan_loop(self):
        log("--- Starting Finviz Screener Anomaly Scan ---")
        if not (time(3, 0) <= datetime.now(TZ).time() <= time(19, 0)): return
        
        try:
            screener = Screener(filters=FINVIZ_SCREENER_PARAMS, table='Overview')
            df = await asyncio.to_thread(screener.get_dataframe)
            
            if df.empty:
                # log("Finviz screener returned no results for the given filters.")
                self.last_finviz_screener_results = set()
                return

            current_tickers = set(df.index.tolist())
            new_tickers = current_tickers - self.last_finviz_screener_results
            if new_tickers:
                log(f"FINVIZ ANOMALY: Found {len(new_tickers)} new tickers: {', '.join(new_tickers)}")
                for ticker in new_tickers:
                    pass
            self.last_finviz_screener_results = current_tickers
        
        except IndexError:
            log("Finviz screener returned no results, resulted in an index error.")
        except Exception as e: 
            log(f"FINVIZ SCANNER: CRITICAL FAILURE. Error: {e}", "error")

    @tasks.loop(seconds=30.0)
    async def seismic_scanner_loop(self):
        if not (time(3, 0) <= datetime.now(TZ).time() <= time(19, 0)): return

        scan_list = list(read_watchlist_file(SEISMIC_WATCHLIST_FILE))
        if not scan_list: return
        
        for ticker in scan_list:
            # 1. Check penalty box first to see if we should skip this ticker
            if self.seismic_penalty_box.get(ticker, 0) >= self.SEISMIC_PENALTY_THRESHOLD:
                continue # Skip this ticker, it has failed too many times recently.

            try:
                hist = await asyncio.to_thread(
                    yf.download,
                    ticker,
                    period="2d",
                    interval="1m",
                    progress=False,
                    auto_adjust=True
                )
                
                if hist.empty or len(hist) < 11:
                    continue

                hist['Volume'] = pd.to_numeric(hist['Volume'], errors='coerce').fillna(0)
                latest_volume = hist['Volume'].iloc[-1]
                avg_volume = hist['Volume'].iloc[-11:-1].mean()

                if isinstance(avg_volume, pd.Series):
                    avg_volume = avg_volume.iloc[0] if not avg_volume.empty else 0

                if avg_volume > 100 and latest_volume > (avg_volume * SEISMIC_VOLUME_MULTIPLIER):
                    if ticker not in self.investigation_watchlist:
                        log(f"SEISMIC HIT: ${ticker} anomalous volume.")
                        self.investigation_watchlist.add(ticker)
                        self.heartbeat_seismic_hits.add(ticker)
                
                # 2. If the ticker was processed successfully, reset its penalty count.
                if ticker in self.seismic_penalty_box:
                    del self.seismic_penalty_box[ticker]

            except Exception as e:
                # 3. If any error occurs, log it and add a strike to the ticker's penalty count.
                log(f"SEISMIC: Could not process ticker ${ticker}. Error: {e}", "warning")
                self.seismic_penalty_box[ticker] += 1
        
    @tasks.loop(minutes=1.0)
    async def technical_breakout_scanner_loop(self):
        if not (time(8, 30) <= datetime.now(TZ).time() <= time(15, 0)): return
        scan_list = list(read_watchlist_file(WATCHLIST_FILE).union(read_watchlist_file(SEISMIC_WATCHLIST_FILE)))
        if not scan_list: return
        
    @tasks.loop(time=time(20, 0, tzinfo=TZ))
    async def advanced_pattern_scanner_loop(self):
        log("--- Starting Advanced Chart Pattern Scan ---")

    @tasks.loop(hours=1)
    async def daily_reset_loop(self):
        now_ct = datetime.now(TZ)
        if now_ct.hour == 0:
            log("--- Performing Daily Reset ---")
            self.seismic_penalty_box.clear()
            self.gappers_scan_done_today = False
            self.recently_alerted_tickers.clear()
            self.confirmation_watchlist.clear()
            self.continuation_watchlist.clear()
            self.continuation_checkpoints.clear()
            self.daily_seismic_additions.clear()
            self.anomaly_investigated_tickers.clear()
            log("Daily reset of caches and flags completed.")
        if now_ct.hour == 1:
            await self.clean_watchlist_files([WATCHLIST_FILE, SEISMIC_WATCHLIST_FILE])

    async def clean_watchlist_files(self, files_to_clean: list):
        log("--- Starting Daily Watchlist Cleanup ---")
        for filepath in files_to_clean:
            if not os.path.exists(filepath): continue
            
            try:
                tickers = read_watchlist_file(filepath)
                if not tickers: continue
                
                valid_tickers = set()
                invalid_tickers = set()

                async def check_ticker(ticker):
                    try:
                        info = await asyncio.to_thread(get_blocking_info, ticker)
                        if info and info.get('quoteType'):
                            valid_tickers.add(ticker)
                        else:
                            log(f"CLEANUP: Ticker ${ticker} is invalid or delisted. Queueing for removal.", "warning")
                            invalid_tickers.add(ticker)
                    except Exception as e:
                        log(f"CLEANUP: Error validating ticker ${ticker}. Queueing for removal. Error: {e}", "warning")
                        invalid_tickers.add(ticker)

                await asyncio.gather(*(check_ticker(t) for t in tickers))

                if invalid_tickers:
                    log(f"REMOVING {len(invalid_tickers)} INVALID TICKERS from {os.path.basename(filepath)}: {', '.join(sorted(list(invalid_tickers)))}")
                    final_tickers = tickers - invalid_tickers
                    with open(filepath, 'w') as f:
                        for t in sorted(list(final_tickers)):
                            f.write(t + '\n')
            except Exception as e:
                log(f"An unexpected error occurred during the cleanup of {os.path.basename(filepath)}: {e}", "error")

bot = CatalystBot()
watchlist_group = app_commands.Group(name="watchlist", description="Manage your personal watchlist.")

@watchlist_group.command(name="list", description="Show all tickers in your watchlist.")
async def watchlist_list(i: discord.Interaction):
    await i.response.defer(ephemeral=True)
    try:
        tickers = read_watchlist_file(WATCHLIST_FILE)
        if not tickers: await i.followup.send("Your watchlist is currently empty."); return
        await i.followeup.send(embed=discord.Embed(title="Your Personal Watchlist", description="\n".join(sorted(tickers)), color=0x7289DA))
    except FileNotFoundError: await i.followup.send("Watchlist not found. Use `/watchlist add` to create it.")

@watchlist_group.command(name="add", description="Add tickers to your watchlist.")
@app_commands.describe(tickers="A comma-separated list of tickers to add (e.g., AAPL,GOOG)")
async def watchlist_add(i: discord.Interaction, tickers: str):
    await i.response.defer(ephemeral=True)
    existing = read_watchlist_file(WATCHLIST_FILE)
    new = {t.strip().upper() for t in tickers.split(',') if t.strip()}
    added = new - existing
    existing.update(new)
    with open(WATCHLIST_FILE, 'w') as f:
        for t in sorted(list(existing)): f.write(t + '\n')
    await i.followup.send(f"Added: `{'`, `'.join(sorted(added)) if added else 'None'}`.")

@watchlist_group.command(name="remove", description="Remove tickers from your watchlist.")
@app_commands.describe(tickers="A comma-separated list of tickers to remove (e.g., TSLA,AMD)")
async def watchlist_remove(i: discord.Interaction, tickers: str):
    await i.response.defer(ephemeral=True)
    existing = read_watchlist_file(WATCHLIST_FILE)
    if not existing: await i.followup.send("Your watchlist is already empty."); return
    to_remove = {t.strip().upper() for t in tickers.split(',') if t.strip()}
    removed = existing.intersection(to_remove)
    existing -= to_remove
    with open(WATCHLIST_FILE, 'w') as f:
        for t in sorted(list(existing)): f.write(t + '\n')
    await i.followup.send(f"Removed: `{'`, `'.join(sorted(removed)) if removed else 'None'}`.")

@watchlist_group.command(name="clear", description="Clear all tickers from your watchlist.")
async def watchlist_clear(i: discord.Interaction):
    view = ConfirmClearView()
    await i.response.send_message("Are you sure?", view=view, ephemeral=True)
    await view.wait()
    if view.value:
        with open(WATCHLIST_FILE, 'w') as f: f.write('')

if __name__ == "__main__":
    if not all([DISCORD_BOT_TOKEN, ALERT_CHANNEL_ID, HEARTBEAT_CHANNEL_ID, ALPHA_VANTAGE_API_KEY, FINVIZ_AUTH_TOKEN]):
        log("CRITICAL: One or more essential environment variables are missing.", "critical")
    else:
        log("Initializing Bot...")
        # twitter_thread = threading.Thread(target=start_twitter_stream, args=(bot,), daemon=True)
        # twitter_thread.start()
        bot.run(DISCORD_BOT_TOKEN)
