Catalyst Bot
Welcome to Catalyst Bot! This is a powerful, autonomous tool for traders that scans financial news sources for market-moving catalysts, scores them based on sentiment and keywords, and delivers real-time alerts to a Discord server. It's designed to help you get into trades early, before the crowd.

Core Features
Autonomous News Scanning: Runs in a continuous loop, checking multiple financial news RSS feeds every 5 minutes.
Intelligent Scoring: Combines VADER sentiment analysis with a customizable keyword dictionary to generate a "Catalyst Score" for each news item.
Precision Filtering: Focuses on low-priced stocks (under $5.00 by default) to target highly volatile opportunities.
Robust Ticker Extraction: Uses multiple layers of logic to find the correct stock ticker from a headline, even when it's not explicitly mentioned.
Interactive Commands: Allows users to get on-demand data and charts directly within Discord.
Professional Candlestick Charts: Generates detailed intraday charts complete with VWAP, volume, session shading, and the previous day's close.
Feedback System: Bot owners can "train" the bot by reacting to alerts, and this feedback is logged for future analysis and machine learning features.

Setup Guide
Follow these steps to get your own instance of Catalyst Bot running.
Step 1: Create a Discord Bot ApplicationBefore you can run the code, you need to create a "Bot User" in Discord.Go to the Discord Developer Portal and log in.
Click "New Application" and give your bot a name (e.g., "My Trading Bot").Go to the "Bot" tab on the left.
Under the "Token" section, click "Reset Token" and then "Yes, do it!". This will reveal your bot's token. This is a secret password! Do not share it with anyone. Copy it for now.
Scroll down and enable the "Message Content Intent" toggle.Go to the "OAuth2" -> "URL Generator" tab.In the "Scopes" box, check bot and applications.commands.
In the "Bot Permissions" box that appears, check Send Messages, Embed Links, Attach Files, and Add Reactions.
Copy the generated URL at the bottom of the page, paste it into your web browser, and invite the bot to your server.
Step 2: Prepare Your Project FolderCreate a new folder on your computer for the bot.
Save the main Python script as catalyst_bot.py inside this folder.Create a new file in this folder called requirements.txt.
Create a new file in this folder called .env.
Step 3: Install Dependencies
Open your terminal or command prompt, navigate to your new project folder, and run this command. It will automatically install all the necessary Python libraries.pip install -r requirements.txt
Step 4: Configure the .env File
Open the .env file you created and add the following lines. This file stores your secret keys and settings.
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
ALERT_CHANNEL_ID=YOUR_ALERTS_CHANNEL_ID_HERE
HEARTBEAT_CHANNEL_ID=YOUR_HEARTBEAT_CHANNEL_ID_HERE
FINNHUB_API_KEY=YOUR_FINNHUB_API_KEY_HERE
DISCORD_BOT_TOKEN: Paste the bot token you copied in Step 1.ALERT_CHANNEL_ID: Right-click on the Discord channel where you want news alerts to be posted and click "Copy Channel ID". (You may need to enable Developer Mode in your Discord settings: User Settings > Advanced > Developer Mode).
HEARTBEAT_CHANNEL_ID: The ID of a channel where the bot can post a simple "I'm alive" message every 5 minutes. This can be the same as the alert channel.
INNHUB_API_KEY: (Optional) For better ticker lookups, you can get a free API key from Finnhub.io.
Running the BotOnce everything is set up, simply run the bot from your terminal:python catalyst_bot.py
The bot will log into Discord and start its news-scanning loop.
Using the BotAutomatic Alerts: The bot will automatically post news alerts that meet the scoring and price criteria into your designated alert channel.
On-Demand Charts: Use the /check command in any channel to get a detailed data snapshot and a professional candlestick chart for any ticker.
Example: /check TSLAProvide Feedback: React to any news alert with a 👍 or 👎 to log your feedback. This data is saved to feedback.csv and is the foundation for making the bot smarter over time.
CustomizationYou can easily shape the bot to fit your specific trading style by editing the Python script.
Keywords: Add or remove keywords and adjust their scores in the HIGH_IMPACT_POSITIVE and HIGH_IMPACT_NEGATIVE dictionaries.
Ticker Aliases: If the bot ever fails to find a ticker for a specific company, you can teach it by adding an entry to the TICKER_ALIASES dictionary.
Filters: Adjust the MAX_SHARE_PRICE or CATALYST_SCORE_THRESHOLD variables to make the bot more or less selective.
