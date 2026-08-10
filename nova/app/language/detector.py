"""Deterministic Language Detector for English, Devanagari Hindi, and Hinglish."""

import re
from typing import Tuple, Optional
from nova.app.language.models import (
    LanguageCode,
    LanguageStyle,
    LanguageDetectionResult,
)

# Common Hinglish conversational markers (Latin script)
HINGLISH_WORDS = {
    "bhai", "kya", "haal", "hai", "samjha", "karo", "batao", "mein", "mujhe",
    "ko", "se", "kal", "school", "kar", "de", "kaise", "ho", "ye", "yeh",
    "par", "aur", "bana", "karna", "kardo", "le", "lo", "bata", "hum", "tum",
    "aap", "chahiye", "rakho", "thoda", "easy", "kijiye", "karke", "sakte",
    "wale", "wali", "nahin", "nhi", "na", "mat", "khol", "chala", "bol"
}


class LanguageDetector:
    """Detects primary language, style, script, and explicit language directives."""

    def detect(self, text: str) -> LanguageDetectionResult:
        """Analyze text and return a LanguageDetectionResult."""
        if not text or not text.strip():
            return LanguageDetectionResult(
                language=LanguageCode.UNKNOWN,
                confidence=0.0,
                style=LanguageStyle.UNKNOWN,
                detected_script="none",
                signals=["empty_input"],
            )

        clean = text.strip()
        lower = clean.lower()

        # 1. Explicit Directive Check
        explicit_req = self._parse_explicit_directive(lower)

        # 2. Devanagari Script Analysis (Hindi)
        devanagari_count = len(re.findall(r"[\u0900-\u097F]", clean))
        if devanagari_count > 0:
            total_chars = len(re.sub(r"\s+", "", clean))
            ratio = devanagari_count / max(total_chars, 1)
            confidence = min(round(0.7 + (ratio * 0.3), 2), 1.0)

            return LanguageDetectionResult(
                language=LanguageCode.HI,
                confidence=confidence,
                style=LanguageStyle.HINDI,
                detected_script="devanagari",
                signals=[f"devanagari_char_count:{devanagari_count}"],
                explicit_request=explicit_req,
            )

        # 3. Latin Script - Check for Hinglish Code-Switching vs Standard English
        tokens = [t.strip(",.!?\"'") for t in re.split(r"\s+", lower) if t.strip(",.!?\"'")]
        hinglish_hits = [t for t in tokens if t in HINGLISH_WORDS]

        if hinglish_hits:
            hinglish_ratio = len(hinglish_hits) / max(len(tokens), 1)
            confidence = min(round(0.6 + (hinglish_ratio * 0.4), 2), 1.0)
            style = LanguageStyle.HINGLISH if hinglish_ratio >= 0.2 else LanguageStyle.MIXED

            return LanguageDetectionResult(
                language=LanguageCode.HINGLISH,
                confidence=confidence,
                style=style,
                detected_script="latin",
                signals=[f"hinglish_matched_words:{len(hinglish_hits)}"],
                explicit_request=explicit_req,
            )

        # 4. Standard English Check
        if re.search(r"[a-zA-Z]", clean):
            return LanguageDetectionResult(
                language=LanguageCode.EN,
                confidence=0.9,
                style=LanguageStyle.ENGLISH,
                detected_script="latin",
                signals=["latin_script_no_code_switch"],
                explicit_request=explicit_req,
            )

        # 5. Fallback for Low-Confidence / Numbers / Ambiguous input
        return LanguageDetectionResult(
            language=LanguageCode.UNKNOWN,
            confidence=0.3,
            style=LanguageStyle.UNKNOWN,
            detected_script="unknown",
            signals=["ambiguous_or_symbolic"],
            explicit_request=explicit_req,
        )

    def _parse_explicit_directive(self, text: str) -> Optional[LanguageCode]:
        """Detect explicit user requests to switch languages."""
        if re.search(r"\b(in\s+hindi|hindi\s+mein|hindi\s+me|hindi\s+batao|hindi\s+samjha)\b", text):
            return LanguageCode.HI
        if re.search(r"\b(in\s+hinglish|hinglish\s+mein|hinglish\s+me|hinglish\s+samjha)\b", text):
            return LanguageCode.HINGLISH
        if re.search(r"\b(in\s+english|english\s+mein|english\s+me|english\s+batao)\b", text):
            return LanguageCode.EN
        return None


default_language_detector = LanguageDetector()
