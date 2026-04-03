"""
Multi-platform market matcher.
Handles the different question formats across Polymarket, Kalshi, and Gemini:
  - Polymarket: "Will the Boston Celtics win the 2026 NBA Finals?"
  - Kalshi:     "Boston win 2026 Pro Basketball Finals"
  - Gemini:     "Will Boston win Boston vs Miami?" or "Presidential Election Winner 2028?"
"""
from typing import List, Tuple, Optional
from difflib import SequenceMatcher
from .models import Market
import re

# Common stopwords to ignore when comparing market titles
STOPWORDS = {
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'need', 'dare', 'ought',
    'to', 'of', 'in', 'on', 'at', 'by', 'for', 'with', 'about', 'from',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'between', 'out', 'off', 'over', 'under', 'again', 'further', 'then',
    'once', 'and', 'but', 'or', 'nor', 'so', 'yet', 'both', 'either',
    'neither', 'not', 'only', 'own', 'same', 'than', 'too', 'very',
    'who', 'what', 'when', 'where', 'why', 'how', 'which', 'that', 'this',
    'these', 'those', 'it', 'its', 'he', 'she', 'they', 'we', 'you', 'i',
    'his', 'her', 'their', 'our', 'your', 'my', 'any', 'all', 'each',
    'first', 'last', 'next', 'new', 'old', 'more', 'most', 'other',
    'up', 'as', 'if', 'no', 'nor', 'just', 'get', 'go', 'become', 'make',
    'win', 'winner', 'wins', 'winning',  # too generic for sports matching
}

# Team/league name normalizations
# Sport keywords — if both titles mention sport keywords, they must match the same sport
SPORT_LEAGUES = {
    "nhl": "hockey",
    "stanley cup": "hockey",
    "hockey": "hockey",
    "nba": "basketball",
    "basketball": "basketball",
    "mlb": "baseball",
    "baseball": "baseball",
    "world series": "baseball",
    "nfl": "football",
    "football": "football",
    "super bowl": "football",
    "masters": "golf",
    "golf": "golf",
    "pga": "golf",
    "soccer": "soccer",
    "mls": "soccer",
    "premier league": "soccer",
    "champions league": "soccer",
    "world cup": "soccer",
    "tennis": "tennis",
    "wimbledon": "tennis",
    "us open": "tennis",
    "formula 1": "motorsport",
    "f1": "motorsport",
    "nascar": "motorsport",
}

NORMALIZATIONS = {
    # NBA teams
    "celtics": "boston",
    "lakers": "los angeles",
    "warriors": "golden state",
    "bucks": "milwaukee",
    "heat": "miami",
    "knicks": "new york",
    "76ers": "philadelphia",
    "sixers": "philadelphia",
    "nuggets": "denver",
    "suns": "phoenix",
    "clippers": "la",
    "nets": "brooklyn",
    "bulls": "chicago",
    "cavaliers": "cleveland",
    "cavs": "cleveland",
    "hawks": "atlanta",
    "magic": "orlando",
    "raptors": "toronto",
    "pistons": "detroit",
    "pacers": "indiana",
    "hornets": "charlotte",
    "wizards": "washington",
    # NHL teams
    "bruins": "boston",
    "canadiens": "montreal",
    "maple leafs": "toronto",
    "rangers": "new york",
    "flyers": "philadelphia",
    "penguins": "pittsburgh",
    "capitals": "washington",
    "hurricanes": "carolina",
    "lightning": "tampa bay",
    "panthers": "florida",
    "red wings": "detroit",
    "blackhawks": "chicago",
    "blues": "st louis",
    "stars": "dallas",
    "avalanche": "colorado",
    "wild": "minnesota",
    "jets": "winnipeg",
    "oilers": "edmonton",
    "flames": "calgary",
    "canucks": "vancouver",
    "sharks": "san jose",
    "ducks": "anaheim",
    "kings": "los angeles",
    "golden knights": "vegas",
    "kraken": "seattle",
    # MLB teams
    "yankees": "new york",
    "red sox": "boston",
    "cubs": "chicago",
    "white sox": "chicago",
    "dodgers": "los angeles",
    "giants": "san francisco",
    "mets": "new york",
    "phillies": "philadelphia",
    "braves": "atlanta",
    "cardinals": "st louis",
    "astros": "houston",
    "rangers": "texas",
    "mariners": "seattle",
    "athletics": "oakland",
    "padres": "san diego",
    "rockies": "colorado",
    "diamondbacks": "arizona",
    "tigers": "detroit",
    "indians": "cleveland",
    "guardians": "cleveland",
    "twins": "minnesota",
    "royals": "kansas city",
    "angels": "los angeles",
    # League name normalizations
    "nba finals": "basketball championship",
    "stanley cup": "hockey championship",
    "world series": "baseball championship",
    "super bowl": "football championship",
    "pro basketball": "basketball",
    "pro football": "football",
    "pro baseball": "baseball",
    "pro hockey": "hockey",
}


class MarketMatcher:
    def __init__(self, threshold: float = 0.70):
        self.threshold = threshold

    def _normalize(self, title: str) -> str:
        """Apply team/league name normalizations."""
        t = title.lower()
        for src, dst in NORMALIZATIONS.items():
            t = re.sub(r'\b' + re.escape(src) + r'\b', dst, t)
        return t

    def _clean_title(self, title: str) -> str:
        """Normalize market titles for better matching."""
        title = self._normalize(title)
        # Remove question marks and punctuation
        title = re.sub(r'[?!.,;:\'"()\[\]{}]', ' ', title)
        # Remove common question prefixes
        title = re.sub(r'^(will|who|what|when|where|which|how|does|is|are|can)\s+', '', title)
        # Remove "vs" separators
        title = re.sub(r'\bvs\.?\b', ' ', title)
        # Normalize year references
        title = re.sub(r'\b(20\d\d)\b', lambda m: m.group(0), title)
        # Normalize crypto tickers
        title = re.sub(r'\b(btc|eth|fed|cpi|usd|eur|gbp|xrp|sol|btc)\b',
                       lambda m: m.group(0).upper(), title)
        # Remove extra whitespace
        title = re.sub(r'\s+', ' ', title).strip()
        return title

    def _get_keywords(self, title: str) -> set:
        """Extract meaningful keywords from a cleaned title."""
        cleaned = self._clean_title(title)
        words = cleaned.split()
        return {w for w in words if w not in STOPWORDS and len(w) > 2}

    def _detect_sport(self, title: str) -> Optional[str]:
        """Detect which sport a market title refers to, if any."""
        t = title.lower()
        for keyword, sport in SPORT_LEAGUES.items():
            if keyword in t:
                return sport
        return None

    def _sports_compatible(self, a: str, b: str, cat_a: str = None, cat_b: str = None) -> bool:
        """
        Return False if both titles reference sports but different sports.
        Uses category tags first (more reliable), then falls back to title detection.
        This prevents NHL teams matching MLB teams, etc.
        """
        # Use category tags if available (most reliable)
        if cat_a and cat_b:
            # Both have categories: check sport-level compatibility
            sport_a = SPORT_LEAGUES.get(cat_a, cat_a)
            sport_b = SPORT_LEAGUES.get(cat_b, cat_b)
            if sport_a != sport_b:
                return False
            return True

        # Fall back to title-based detection
        sport_a = cat_a or self._detect_sport(a)
        sport_b = cat_b or self._detect_sport(b)
        if sport_a and sport_b and sport_a != sport_b:
            return False
        return True

    def _extract_person_names(self, title: str) -> set:
        """Extract likely person names (capitalized multi-word sequences) from a title."""
        # Find sequences of 2+ capitalized words (likely names)
        names = set()
        words = title.split()
        i = 0
        while i < len(words):
            w = re.sub(r'[^a-zA-Z]', '', words[i])
            if w and w[0].isupper() and len(w) > 1:
                # Start of a potential name
                name_parts = [w.lower()]
                j = i + 1
                while j < len(words):
                    nw = re.sub(r'[^a-zA-Z]', '', words[j])
                    if nw and nw[0].isupper() and len(nw) > 1:
                        name_parts.append(nw.lower())
                        j += 1
                    else:
                        break
                if len(name_parts) >= 2:
                    names.add(' '.join(name_parts))
                i = j
            else:
                i += 1
        return names

    def _person_names_compatible(self, a: str, b: str) -> bool:
        """
        If both titles contain person names, they must share at least one name.
        Prevents 'Jon Stewart' matching 'Jon Ossoff' just because of 'Jon'.
        """
        names_a = self._extract_person_names(a)
        names_b = self._extract_person_names(b)
        if not names_a or not names_b:
            return True  # Can't determine, allow
        # Check if any name from A appears in B's names (partial match OK for last names)
        for na in names_a:
            for nb in names_b:
                # Full name match
                if na == nb:
                    return True
                # Last name match (last word of name)
                last_a = na.split()[-1]
                last_b = nb.split()[-1]
                if last_a == last_b and len(last_a) > 3:
                    return True
        return False

    def _question_type_compatible(self, a: str, b: str) -> bool:
        """
        Reject matches where the questions are clearly about different outcomes.
        E.g., 'win the election' vs 'win the primary/nomination' are different.
        """
        a_l = a.lower()
        b_l = b.lower()

        # Primary/nomination vs general election
        primary_terms = {'primary', 'nominee', 'nomination', 'nominate'}
        election_terms = {'presidential election', 'general election', 'election winner'}

        a_is_primary = any(t in a_l for t in primary_terms)
        b_is_primary = any(t in b_l for t in primary_terms)
        a_is_general = any(t in a_l for t in election_terms)
        b_is_general = any(t in b_l for t in election_terms)

        if (a_is_primary and b_is_general) or (a_is_general and b_is_primary):
            return False

        return True

    def _calculate_similarity(self, a: str, b: str) -> float:
        """Calculate the similarity score between two market titles."""
        a_clean = self._clean_title(a)
        b_clean = self._clean_title(b)

        # Exact match after cleaning
        if a_clean == b_clean:
            return 1.0

        # Keyword-based Jaccard similarity
        a_kw = self._get_keywords(a)
        b_kw = self._get_keywords(b)

        if a_kw and b_kw:
            intersection = a_kw & b_kw
            union = a_kw | b_kw
            jaccard = len(intersection) / len(union)

            if len(intersection) >= 3:
                return min(1.0, 0.6 + jaccard * 0.5)
            elif len(intersection) >= 2:
                return min(1.0, 0.4 + jaccard * 0.5)

        # Fallback: character-level sequence similarity
        seq_score = SequenceMatcher(None, a_clean, b_clean).ratio()
        return seq_score

    def find_matches(
        self,
        markets_a: List[Market],
        markets_b: List[Market],
    ) -> List[Tuple[Market, Market]]:
        """
        Identify corresponding markets across two platform lists.
        Each market from list A is matched to at most one from list B.
        Cross-sport matches (e.g. NHL team vs MLB team) are rejected.
        """
        matches = []
        used_b = set()

        for ma in markets_a:
            best_match = None
            best_score = 0.0

            for mb in markets_b:
                if mb.id in used_b:
                    continue
                # Reject cross-sport matches (use category tags if available)
                if not self._sports_compatible(
                    ma.question, mb.question,
                    cat_a=ma.category, cat_b=mb.category
                ):
                    continue
                # Reject person-name mismatches (Jon Stewart != Jon Ossoff)
                if not self._person_names_compatible(ma.question, mb.question):
                    continue
                # Reject question-type mismatches (primary != general election)
                if not self._question_type_compatible(ma.question, mb.question):
                    continue
                score = self._calculate_similarity(ma.question, mb.question)
                if score > best_score:
                    best_score = score
                    best_match = mb

            if best_match and best_score >= self.threshold:
                matches.append((ma, best_match))
                used_b.add(best_match.id)

        return matches
