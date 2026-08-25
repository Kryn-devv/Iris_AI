"""Language Normalizer for tool parameter extraction and input cleanup."""

import re
from typing import Tuple, Optional


class LanguageNormalizer:
    """Normalizes multilingual/Hinglish prompts into tool-friendly internal representations."""

    def normalize_tool_expression(self, text: str) -> str:
        """Extract clean mathematical or tool expression from Hindi/Hinglish natural language."""
        if not text or not text.strip():
            return text

        clean = text.strip()

        # Check for Hinglish/Hindi calculation phrases: e.g., "25 ko 40 se multiply karo" or "25 को 40 से गुणा करो"
        # Replace Devanagari numbers if present
        clean = self.convert_devanagari_digits(clean)

        # Normalize verbal word operators to math symbols
        clean = re.sub(r"\bmultiplied\s+by\b", "*", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\bdivided\s+by\b", "/", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\bplus\b", "+", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\bminus\b", "-", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\btimes\b", "*", clean, flags=re.IGNORECASE)

        # Normalize Hinglish / Hindi operator phrases
        clean = re.sub(r"(\d+(?:\.\d+)?)\s*(?:ko|को)\s+(\d+(?:\.\d+)?)\s*(?:se|से)?\s*(?:multiply|गुणा|guna|\*)\s*(?:karo|kar|do|करो|कर)?", r"\1 * \2", clean, flags=re.IGNORECASE)
        clean = re.sub(r"(\d+(?:\.\d+)?)\s*(?:ko|को)\s+(\d+(?:\.\d+)?)\s*(?:se|से)?\s*(?:divide|भाग|bhag|\/)\s*(?:karo|kar|do|करो|कर)?", r"\1 / \2", clean, flags=re.IGNORECASE)
        clean = re.sub(r"(\d+(?:\.\d+)?)\s*(?:me|में|mein)\s+(\d+(?:\.\d+)?)\s*(?:plus|जोड़ो|jodo|add|\+)\s*(?:karo|kar|do|करो|कर)?", r"\1 + \2", clean, flags=re.IGNORECASE)
        clean = re.sub(r"(\d+(?:\.\d+)?)\s*(?:se|से)\s+(\d+(?:\.\d+)?)\s*(?:minus|घटाओ|ghatao|subtract|\-)\s*(?:karo|kar|do|करो|कर)?", r"\1 - \2", clean, flags=re.IGNORECASE)

        # Match chained arithmetic expression pattern (digits and math operators)
        math_matches = [m.group(0).strip() for m in re.finditer(r"([\d\.\s\+\-\*\/\%\(\)\*\*]+)", clean) if m.group(0).strip()]
        valid_candidates = []
        for m in math_matches:
            if re.search(r"\d", m) and re.search(r"[\+\-\*\/\%]", m):
                valid_candidates.append(m.strip().rstrip("?").rstrip("."))

        if valid_candidates:
            return max(valid_candidates, key=len)

        return clean

    @staticmethod
    def convert_devanagari_digits(text: str) -> str:
        """Convert Devanagari numerals (०-९) to ASCII digits (0-9)."""
        devanagari_map = {
            '०': '0', '१': '1', '२': '2', '३': '3', '४': '4',
            '५': '5', '६': '6', '७': '7', '८': '8', '९': '9'
        }
        res = []
        for char in text:
            res.append(devanagari_map.get(char, char))
        return "".join(res)


default_language_normalizer = LanguageNormalizer()
