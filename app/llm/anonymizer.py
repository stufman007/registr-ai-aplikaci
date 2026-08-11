"""Pseudonymizace osobních údajů v textu odesílaném do LLM (spec kap. 7.2).

Tok: Jméno/e-mail/telefon → placeholder → LLM vidí jen placeholder → po validaci
odpovědi se placeholder nahradí zpět skutečnou hodnotou. Reverzní mapa žije jen
v paměti jedné instance (jeden request) — nikam se neukládá, nikam se neloguje.

Limit: obecné NER neděláme. E-maily a telefony se chytají regexem (mají pevný
tvar), jména jen case-insensitive substring shodou proti známým kontaktům
z registru předaným do konstruktoru. Tento limit je vědomý a popsaný v README.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# E-mail: standardní tvar local@domain.tld. Nechytá okolní interpunkci
# (závorky, čárky), protože ty nejsou součástí povolené znakové třídy.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Telefon: české i mezinárodní formáty s mezerami/pomlčkami a volitelnou
# předvolbou, např. "+420 601 123 456", "601123456", "+420-601-123-456".
# Aby nechytal krátká čísla (false positive), vyžaduje po odstranění
# oddělovačů alespoň 9 číslic. Hranice (?<!\d)/(?!\d) zabrání ukousnutí
# části delšího číselného řetězce.
_PHONE_RE = re.compile(r"(?<!\d)\+?(?:\d[ \-]?){8,}\d(?!\d)")


class Anonymizer:
    """Pseudonymizuje a zpětně deanonymizuje text. Jedna instance = jeden request."""

    def __init__(self, known_contacts: Iterable[tuple[str, str]] | None = None) -> None:
        """`known_contacts`: (jméno, e-mail) známých kontaktů z registru.

        Delší jména se řadí první, aby "Jan Novák" nepřebila dílčí shoda
        s kratším jménem (např. samotné "Novák").
        """
        names = {name for name, _email in (known_contacts or ()) if name}
        self._known_names: list[str] = sorted(names, key=len, reverse=True)

        self._value_to_placeholder: dict[str, str] = {}
        self._placeholder_to_value: dict[str, str] = {}
        self._counters: dict[str, int] = {"OSOBA": 0, "EMAIL": 0, "TEL": 0}

    def pseudonymize(self, text: str) -> str:
        """Nahradí e-maily, telefony a známá jména placeholdery."""
        text = self._replace_emails(text)
        text = self._replace_phones(text)
        text = self._replace_names(text)
        return text

    def restore(self, text: str) -> str:
        """Dosadí zpět skutečné hodnoty dle interní mapy této instance."""
        for placeholder, original in self._placeholder_to_value.items():
            text = text.replace(placeholder, original)
        return text

    def __repr__(self) -> str:
        # Mapa se nikdy nevypisuje (obsahuje osobní údaje) — jen počty.
        return (
            f"Anonymizer(osoby={self._counters['OSOBA']}, "
            f"emaily={self._counters['EMAIL']}, "
            f"telefony={self._counters['TEL']})"
        )

    def _replace_emails(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            original = match.group(0)
            return self._placeholder_for("EMAIL", f"email:{original.casefold()}", original)

        return _EMAIL_RE.sub(repl, text)

    def _replace_phones(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            original = match.group(0)
            digits_only = re.sub(r"\D", "", original)
            return self._placeholder_for("TEL", f"tel:{digits_only}", original)

        return _PHONE_RE.sub(repl, text)

    def _replace_names(self, text: str) -> str:
        if not self._known_names:
            return text

        pattern = re.compile(
            "|".join(re.escape(name) for name in self._known_names),
            re.IGNORECASE,
        )

        def repl(match: re.Match[str]) -> str:
            original = match.group(0)
            return self._placeholder_for("OSOBA", f"osoba:{original.casefold()}", original)

        return pattern.sub(repl, text)

    def _placeholder_for(self, kind: str, dedup_key: str, original: str) -> str:
        """Vrátí existující placeholder pro `dedup_key`, jinak vytvoří nový."""
        existing = self._value_to_placeholder.get(dedup_key)
        if existing is not None:
            return existing

        self._counters[kind] += 1
        placeholder = f"[{kind}_{self._counters[kind]}]"
        self._value_to_placeholder[dedup_key] = placeholder
        self._placeholder_to_value[placeholder] = original
        return placeholder
