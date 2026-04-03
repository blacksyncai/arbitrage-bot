import requests
import re
from typing import List, Dict, Any, Optional
from .models import Market

# Keyword-based category detection for Polymarket markets
CATEGORY_KEYWORDS = {
    'basketball': ['nba', 'basketball', 'celtics', 'lakers', 'warriors', 'bucks', 'heat', 'knicks', 'nuggets', 'suns', 'clippers', 'nets', 'bulls', 'cavaliers', 'hawks', 'magic', 'raptors', 'pistons', 'pacers', 'hornets', 'wizards', 'spurs', 'mavericks', 'rockets', 'grizzlies', 'pelicans', 'thunder', 'blazers', 'jazz', 'kings', 'timberwolves'],
    'hockey': ['nhl', 'hockey', 'stanley cup', 'bruins', 'canadiens', 'maple leafs', 'rangers', 'flyers', 'penguins', 'capitals', 'hurricanes', 'lightning', 'red wings', 'blackhawks', 'blues', 'avalanche', 'wild', 'jets', 'oilers', 'flames', 'canucks', 'sharks', 'ducks', 'golden knights', 'kraken', 'coyotes', 'predators', 'blue jackets', 'sabres', 'senators', 'islanders', 'devils', 'la kings', 'los angeles kings'],
    'baseball': ['mlb', 'baseball', 'world series', 'yankees', 'red sox', 'cubs', 'dodgers', 'giants', 'mets', 'phillies', 'braves', 'cardinals', 'astros', 'mariners', 'padres', 'rockies', 'diamondbacks', 'tigers', 'guardians', 'twins', 'royals', 'angels', 'rays', 'blue jays', 'orioles', 'nationals', 'marlins', 'pirates', 'reds', 'brewers', 'athletics'],
    'football': ['nfl', 'football', 'super bowl', 'patriots', 'chiefs', 'cowboys', 'packers', 'steelers', 'ravens', 'bills', 'bengals', 'browns', 'dolphins', 'jets', 'giants', 'eagles', 'commanders', 'bears', 'vikings', 'lions', 'buccaneers', 'saints', 'falcons', 'panthers', 'rams', 'seahawks', 'niners', 'cardinals', 'broncos', 'raiders', 'chargers', 'texans', 'colts', 'jaguars', 'titans'],
    'golf': ['golf', 'pga', 'masters', 'us open golf', 'british open', 'ryder cup', 'scheffler', 'mcilroy', 'dechambeau'],
    'soccer': ['soccer', 'football', 'fifa', 'world cup', 'premier league', 'champions league', 'mls', 'la liga', 'bundesliga', 'serie a'],
    'tennis': ['tennis', 'wimbledon', 'us open tennis', 'french open', 'australian open', 'djokovic', 'federer', 'nadal', 'alcaraz', 'sinner'],
    'motorsport': ['formula 1', 'f1', 'nascar', 'indycar', 'verstappen', 'hamilton', 'leclerc'],
    'crypto': ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'xrp', 'solana', 'sol', 'binance', 'coinbase'],
    'politics': ['president', 'election', 'senate', 'congress', 'republican', 'democrat', 'trump', 'biden', 'harris', 'vote', 'governor', 'mayor', 'legislation', 'bill', 'law', 'supreme court', 'tariff', 'nato', 'ukraine', 'russia', 'china', 'iran', 'israel', 'greenland', 'canada'],
    'economics': ['fed', 'federal reserve', 'interest rate', 'cpi', 'inflation', 'gdp', 'recession', 'unemployment', 'jobs report', 'rate cut', 'rate hike'],
    'finance': ['stock', 'nasdaq', 'dow jones', 's&p', 'ipo', 'earnings', 'tesla', 'apple', 'nvidia', 'microsoft', 'google', 'amazon', 'meta'],
}


# Priority order for category detection (more specific first)
CATEGORY_PRIORITY = [
    'hockey', 'basketball', 'baseball', 'football', 'golf',
    'tennis', 'motorsport', 'soccer',
    'crypto', 'economics', 'politics', 'finance',
]


def detect_category(question: str) -> Optional[str]:
    """Detect the category of a market question based on keywords.
    Checks in priority order so hockey takes precedence over basketball
    for ambiguous team names like 'Kings' or 'Panthers'.
    """
    q = question.lower()
    for category in CATEGORY_PRIORITY:
        keywords = CATEGORY_KEYWORDS.get(category, [])
        for kw in keywords:
            if kw in q:
                return category
    return None


class PolymarketClient:
    GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
    CLOB_BASE_URL = "https://clob.polymarket.com"

    def __init__(self):
        self.session = requests.Session()

    def get_active_markets(self, limit: int = 100) -> List[Market]:
        """Fetch active markets from Polymarket Gamma API."""
        url = f"{self.GAMMA_BASE_URL}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit
        }
        response = self.session.get(url, params=params)
        response.raise_for_status()
        markets_data = response.json()

        markets = []
        for data in markets_data:
            # Ensure the market has order book support
            if not data.get('enableOrderBook'):
                continue

            # Parse outcomes and prices
            outcomes = data.get('outcomes', [])
            prices = data.get('outcomePrices', [])
            
            # Polymarket prices are often strings like '["0.5", "0.5"]'
            if isinstance(prices, str):
                import json
                try:
                    prices = json.loads(prices)
                except json.JSONDecodeError:
                    prices = [0.5, 0.5]

            # Check if we have at least two prices
            if len(prices) < 2:
                continue

            # Extract clobTokenIds if present
            clob_token_ids = data.get('clobTokenIds')
            if isinstance(clob_token_ids, str):
                import json
                try:
                    clob_token_ids = json.loads(clob_token_ids)
                except json.JSONDecodeError:
                    clob_token_ids = []

            question = data['question']
            markets.append(Market(
                id=data['id'],
                platform='polymarket',
                question=question,
                yes_price=float(prices[0]),
                no_price=float(prices[1]),
                clob_token_ids=clob_token_ids,
                end_date=data.get('endDate'),
                volume=float(data.get('volumeNum', 0)),
                category=detect_category(question),
            ))
        return markets

    def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """Fetch order book for a specific token from Polymarket CLOB API."""
        url = f"{self.CLOB_BASE_URL}/book"
        params = {"token_id": token_id}
        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()
