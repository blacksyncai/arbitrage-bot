"""
Claude-powered opportunity verifier.
Uses Claude to verify that matched markets are actually asking the same question.
Prevents false positives like "presidential nomination" vs "VP nomination".
"""
import os
from typing import Tuple
from anthropic import Anthropic
from .models import Market

# Initialize Claude client
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def verify_opportunity(market_a: Market, market_b: Market, platform_a: str, platform_b: str) -> Tuple[bool, str]:
    """
    Use Claude to verify that two markets are asking the same question.
    
    Returns:
        (is_valid, reason) - True if the markets are a real match, False if they're different questions
    """
    
    prompt = f"""You are analyzing a potential arbitrage opportunity between two prediction markets.

Platform A ({platform_a}): {market_a.question}
Platform B ({platform_b}): {market_b.question}

Your task: Determine if these two markets are asking **the exact same question** about **the exact same outcome**.

Common false positives to watch for:
- Presidential nomination vs. VP nomination (DIFFERENT)
- Presidential election winner vs. party nomination (DIFFERENT)
- Different people with similar names (e.g., Jon Stewart vs. Jon Ossoff)
- Different sports leagues (NHL vs. NBA)
- Different time periods or seasons

Respond with ONLY one of these two formats:

VALID
Reason: [one sentence explaining why they're the same]

INVALID
Reason: [one sentence explaining the key difference]
"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Fast, cheap, perfect for verification
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        
        result = response.content[0].text.strip()
        
        # Parse the response
        if result.startswith("VALID"):
            reason = result.split("Reason:", 1)[1].strip() if "Reason:" in result else "Markets match"
            return True, reason
        elif result.startswith("INVALID"):
            reason = result.split("Reason:", 1)[1].strip() if "Reason:" in result else "Markets don't match"
            return False, reason
        else:
            # Fallback: if Claude's response is unclear, be conservative
            return False, f"Unclear verification response: {result[:100]}"
            
    except Exception as e:
        # On error, be conservative and reject the opportunity
        return False, f"Verification error: {str(e)}"


def verify_opportunities(opportunities, platform_a: str, platform_b: str) -> list:
    """
    Verify a list of opportunities using Claude.
    Returns only the verified opportunities with verification reasons attached.
    """
    verified = []
    
    for opp in opportunities:
        is_valid, reason = verify_opportunity(opp.market_a, opp.market_b, platform_a, platform_b)
        
        if is_valid:
            # Attach the verification reason to the opportunity for logging
            opp.verification_reason = reason
            verified.append(opp)
        else:
            # Log the rejection reason
            print(f"  [REJECTED] {opp.market_a.question[:60]}")
            print(f"             {opp.market_b.question[:60]}")
            print(f"             Reason: {reason}")
    
    return verified
