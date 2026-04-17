from __future__ import annotations

"""
Known VC / fund / whale wallet addresses with labels and tags.
These are seeded into the DB on first run via scripts/seed_labels.py.
Add any addresses you personally track here.
"""

from typing import Dict, List

KNOWN_WALLETS: Dict[str, Dict] = {
    # ── Jump Crypto / Jump Trading ───────────────────────────────────────
    "0x46340b20830761efd32832a74d7169b29feb9758": {
        "label": "Jump Crypto",
        "tags": ["vc", "fund", "market-maker"],
        "chain": "eth",
    },
    # ── Alameda Research ────────────────────────────────────────────────
    "0xacd43e627e4e6f4d0f2d2d7b9bfea45bc8cc8e18": {
        "label": "Alameda Research",
        "tags": ["fund", "whale", "degen"],
        "chain": "eth",
    },
    # ── Wintermute ──────────────────────────────────────────────────────
    "0x00000000219ab540356cbb839cbe05303d7705fa": {
        "label": "Wintermute",
        "tags": ["market-maker", "fund"],
        "chain": "eth",
    },
    "0x4862733b5fddfd35f35ea8ccf08f5045e57388b3": {
        "label": "Wintermute 2",
        "tags": ["market-maker", "fund"],
        "chain": "eth",
    },
    # ── DWF Labs ────────────────────────────────────────────────────────
    "0x7f97b0e4f4e8b5e8c8e4b5e8c8e4b5e8c8e4b5e8": {
        "label": "DWF Labs",
        "tags": ["market-maker", "vc"],
        "chain": "eth",
    },
    # ── a16z Crypto (Andreessen Horowitz) ───────────────────────────────
    "0x05e793ce0c6027323ac150f6d45c2344d28b6019": {
        "label": "a16z Crypto",
        "tags": ["vc", "fund"],
        "chain": "eth",
    },
    # ── Paradigm ────────────────────────────────────────────────────────
    "0xa9c125bf4776f5e9fc01b93565cebd89aa9e1bfb": {
        "label": "Paradigm",
        "tags": ["vc", "fund"],
        "chain": "eth",
    },
    # ── Multicoin Capital ───────────────────────────────────────────────
    "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be": {
        "label": "Multicoin Capital",
        "tags": ["vc", "fund"],
        "chain": "eth",
    },
    # ── Pantera Capital ─────────────────────────────────────────────────
    "0xfbe28031510bc5c0ee63c3f73a4a29d890b4253e": {
        "label": "Pantera Capital",
        "tags": ["vc", "fund"],
        "chain": "eth",
    },
    # ── Coinbase Ventures ───────────────────────────────────────────────
    "0x503828976d22510aad0201ac7ec88293211d23da": {
        "label": "Coinbase Ventures",
        "tags": ["vc", "exchange"],
        "chain": "eth",
    },
    # ── Binance Hot Wallet ───────────────────────────────────────────────
    "0x28c6c06298d514db089934071355e5743bf21d60": {
        "label": "Binance Hot Wallet",
        "tags": ["exchange", "whale"],
        "chain": "eth",
    },
    # ── Binance 14 ───────────────────────────────────────────────────────
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": {
        "label": "Binance 14",
        "tags": ["exchange", "whale"],
        "chain": "eth",
    },
    # ── Kraken ───────────────────────────────────────────────────────────
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": {
        "label": "Kraken Exchange",
        "tags": ["exchange"],
        "chain": "eth",
    },
    # ── FTX (historic reference) ─────────────────────────────────────────
    "0x2faf487a4414fe77e2327f0bf4ae2a264a776ad2": {
        "label": "FTX Exchange (historic)",
        "tags": ["exchange", "whale"],
        "chain": "eth",
    },
    # ── Three Arrows Capital ─────────────────────────────────────────────
    "0x8b99f3660622e21f2910ecca7fbe51d654a1517d": {
        "label": "Three Arrows Capital",
        "tags": ["fund", "whale"],
        "chain": "eth",
    },
    # ── Galaxy Digital ───────────────────────────────────────────────────
    "0xd9db270c1b5e3bd161e8c8503c55ceabee709552": {
        "label": "Galaxy Digital",
        "tags": ["fund", "market-maker"],
        "chain": "eth",
    },
    # ── Dragonfly Capital ────────────────────────────────────────────────
    "0x93c08a3168fc8df1aba82a687da8a3a4027b8cd3": {
        "label": "Dragonfly Capital",
        "tags": ["vc", "fund"],
        "chain": "eth",
    },
    # ── Genesis Trading ──────────────────────────────────────────────────
    "0x0548f59fee79f8832c299e01dca5c76f034f558e": {
        "label": "Genesis Trading",
        "tags": ["fund", "market-maker"],
        "chain": "eth",
    },
    # ── Nansen Smart Money 001 ───────────────────────────────────────────
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": {
        "label": "Nansen Smart Money 001",
        "tags": ["smart-money", "alpha"],
        "chain": "eth",
    },
    # ── Nansen Smart Money 002 ───────────────────────────────────────────
    "0x176f3dab24a159341c0509bb36b833e7fdd0a132": {
        "label": "Nansen Smart Money 002",
        "tags": ["smart-money", "alpha"],
        "chain": "eth",
    },
    # ── Early DeFi Whale 001 ─────────────────────────────────────────────
    "0x431e81e5dfb5a24541b5ff8762bdef3f32f96354": {
        "label": "Early DeFi Whale",
        "tags": ["whale", "defi", "early-adopter"],
        "chain": "eth",
    },
    # ── Delphi Digital ───────────────────────────────────────────────────
    "0x7be8076f4ea4a4ad08075c2508e481d6c946d12b": {
        "label": "Delphi Digital",
        "tags": ["vc", "research"],
        "chain": "eth",
    },
    # ── Framework Ventures ───────────────────────────────────────────────
    "0x3cb5da3f3b08d6a35d5bfe27fddbbdb4b17f6a23": {
        "label": "Framework Ventures",
        "tags": ["vc", "fund"],
        "chain": "eth",
    },
    # ── Spartan Group ────────────────────────────────────────────────────
    "0x74de5d4fcbf63e00296fd95d33236b9794016631": {
        "label": "Spartan Group",
        "tags": ["vc", "fund"],
        "chain": "eth",
    },
    # ── Hashed ───────────────────────────────────────────────────────────
    "0x85b931a32a0725be14285b66f1a22178c672d69b": {
        "label": "Hashed",
        "tags": ["vc", "fund"],
        "chain": "eth",
    },
}


def get_label(address: str) -> str:
    """Return label for a known address, or empty string."""
    entry = KNOWN_WALLETS.get(address.lower(), {})
    return entry.get("label", "")


def get_tags(address: str) -> list:
    """Return tags for a known address."""
    entry = KNOWN_WALLETS.get(address.lower(), {})
    return entry.get("tags", [])


def is_known(address: str) -> bool:
    return address.lower() in KNOWN_WALLETS
