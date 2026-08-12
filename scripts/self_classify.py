"""Sebe-klasifikace registru vlastním policy enginem (viz README, sekce
„Klasifikace této aplikace samotné a proč").

Spuštění z rootu repa:
    .venv/Scripts/python scripts/self_classify.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas import (
    Autonomie,
    CitlivostDat,
    DopadChyby,
    DotaznikOdpovedi,
    HostingType,
    KomponentaInfo,
    Kritictnost,
    OsobniUdaje,
    PocetUzivatelu,
    Provider,
    RozhodovaniOLidech,
    ViditelnostVystupu,
)
from app.services.policy import compute_minimum, effective_tier
from app.services.regulatory import compute_flags

answers = DotaznikOdpovedi(
    ucel=(
        "Registr interních AI aplikací: evidence, governance tier, "
        "legislativní signály, duplicity."
    ),
    pocet_uzivatelu=PocetUzivatelu.OD_10_DO_50,
    kritictnost=Kritictnost.DULEZITY,
    osobni_udaje=OsobniUdaje.ZAMESTNANCU,
    rozhodovani=RozhodovaniOLidech.NE,
    viditelnost=ViditelnostVystupu.NE,
    citlivost=CitlivostDat.DUVERNA,
    autonomie=Autonomie.NE,
    dopad=DopadChyby.PROVOZNI,
)
components = [
    KomponentaInfo(provider=Provider.OPENAI, hosting_type=HostingType.EXTERNI_API)
]

minimum = compute_minimum(answers, components)
flags = compute_flags(answers, components)
effective = effective_tier(llm_tier=None, minimum=minimum)

print("=== Sebe-klasifikace registru ===")
print()
print("klasifikace_minimum:", minimum.tier.name, "(" + minimum.tier.label() + ")")
print("Aktivovana pravidla:")
for rule in minimum.triggered_rules:
    print(f"  - {rule.reason_code}: {rule.popis} -> min {rule.min_tier.name}")
print()
print("Legislativni signaly (compute_flags):")
for f in flags:
    print(f"  - {f.zkratka}: {f.titulek} (reason_code={f.reason_code})")
print()
print(
    "effective_tier (bez LLM navrhu, bez rucni zmeny):",
    effective.name,
    "(" + effective.label() + ")",
)
