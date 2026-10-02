from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Contracts:
    margin_pool: str
    rysk: str
    mmarket: str

    def all(self) -> dict[str, str]:
        return {
            "MarginPool": self.margin_pool,
            "Rysk": self.rysk,
            "MMarket": self.mmarket,
        }


@dataclass(frozen=True, slots=True)
class Endpoint:
    url: str
    max_batch: int

    max_rps: float | None = None


@dataclass(frozen=True, slots=True)
class Chain:
    key: str
    name: str
    chain_id: int
    rpcs: tuple[Endpoint, ...]
    contracts: Contracts
    explorer: str
    max_log_range: int

    seconds_per_block: float
    native_symbol: str = "ETH"
    aliases: tuple[str, ...] = field(default_factory=tuple)


HYPEREVM = Chain(
    key="hyperevm",
    name="HyperEVM",
    chain_id=999,
    rpcs=(
        Endpoint("https://hyperliquid.drpc.org", max_batch=200),
        Endpoint("https://rpc.hyperlend.finance", max_batch=200),
        Endpoint("https://rpc.purroofgroup.com", max_batch=200),
        Endpoint("https://hyperliquid-json-rpc.stakely.io", max_batch=100),
        Endpoint("https://rpc.hypurrscan.io", max_batch=20),
        Endpoint("https://rpc.hyperliquid.xyz/evm", max_batch=20, max_rps=9.0),
    ),
    contracts=Contracts(
        margin_pool="0x24a44f1dc25540c62c1196FfC297dFC951C91aB4",
        rysk="0x8C8bcb6D2c0E31c5789253EcC8431cA6209B4E35",
        mmarket="0x691a5fc3a81a144e36c6C4fBCa1fC82843c80d0d",
    ),
    explorer="https://hyperevmscan.io",
    max_log_range=1000,
    seconds_per_block=1.0,
    native_symbol="HYPE",
)

ETHEREUM = Chain(
    key="ethereum",
    name="Ethereum",
    chain_id=1,
    rpcs=(
        Endpoint("https://eth.drpc.org", max_batch=100),
        Endpoint("https://rpc.mevblocker.io", max_batch=100),
        Endpoint("https://ethereum-rpc.publicnode.com", max_batch=100),
        Endpoint("https://eth.merkle.io", max_batch=100),
    ),
    contracts=Contracts(
        margin_pool="0x684404F2AEBAD87a6803F13741B1d638Bfe2C671",
        rysk="0x7A3dDEac7A0AE6dfA9391C764499A3564F3c2AAd",
        mmarket="0xc01c9EF5de5862354adD9501a29e8765cFF01c32",
    ),
    explorer="https://etherscan.io",
    max_log_range=10_000,
    seconds_per_block=12.0,
)


@dataclass(frozen=True, slots=True)
class Protocol:
    key: str
    name: str
    chain: str
    otoken_factories: tuple[str, ...]
    contracts: dict[str, str]
    gamma_compatible: bool

    writer_rule: str = "dual_role"

    premium_sources: tuple[str, ...] = ()

    deployer: str = ""
    note: str = ""


RYSK_V12 = Protocol(
    key="rysk",
    name="Rysk V12",
    chain="hyperevm",
    otoken_factories=(),
    contracts={
        "MarginPool": "0x24a44f1dc25540c62c1196FfC297dFC951C91aB4",
        "Rysk": "0x8C8bcb6D2c0E31c5789253EcC8431cA6209B4E35",
        "MMarket": "0x691a5fc3a81a144e36c6C4fBCa1fC82843c80d0d",
    },
    gamma_compatible=True,
    writer_rule="dual_role",
    premium_sources=("0x691a5fc3a81a144e36c6C4fBCa1fC82843c80d0d",),
)


RYSK_V12_ETHEREUM = Protocol(
    key="rysk-eth",
    name="Rysk V12 (Ethereum)",
    chain="ethereum",
    otoken_factories=(),
    contracts={
        "MarginPool": "0x684404F2AEBAD87a6803F13741B1d638Bfe2C671",
        "Rysk": "0x7A3dDEac7A0AE6dfA9391C764499A3564F3c2AAd",
        "MMarket": "0xc01c9EF5de5862354adD9501a29e8765cFF01c32",
    },
    gamma_compatible=True,
    writer_rule="dual_role",
    premium_sources=("0xc01c9EF5de5862354adD9501a29e8765cFF01c32",),
)

HYPERSURFACE = Protocol(
    key="hypersurface",
    name="Hypersurface",
    chain="hyperevm",
    otoken_factories=(
        "0xaeba446d06beecf380118885b2949faf65d97efb",
        "0x9a307633382b7b6bf3d3399af8b675370e181465",
        "0xf6d788dd55738f82d03f97597660221ca8b4c98d",
    ),
    contracts={
        "HedgedPool": "0xe0f9ca7fd12e31f5d720a93d04722d6dfbad59e7",
        "SeriesDeployer": "0xe68c864ded1f3280c5c58560156ee79e3e261df6",
    },
    gamma_compatible=True,
    writer_rule="tx_sender",
    premium_sources=(
        "0xe0f9ca7fd12e31f5d720a93d04722d6dfbad59e7",
        "0x0095acdd705cfcc11eaffb6c19a28c0153ad196f",
        "0x68f77dfabc311df0530b24b22e4d7da413d4f054",
        "0x934c5a942de3a3333c65c245d1abe20fb666cf13",
    ),
    deployer="0x67540f004d06b0145278857e1ec2b9507bbfaee8",
    note=(
        "Four pools, one collected. Every one of them, and both otoken "
        "factories, was deployed by 0x67540f004d, which is how the older set "
        "was identified: a chain-wide sweep for the Gamma creation event "
        "returns eight factories, and the largest made 18,005 series between "
        "15 Sep 2025 and 11 Aug 2026 against our 3,379 from 8 Jul 2026. Its "
        "mints land on the Sep 2025 HSLP pool. So the 96 sales in "
        "data/onchain/hypersurface are the newest pool only, and DefiLlama's "
        "$212,499 of thirty-day premium against our $31,537 ever is the size "
        "of what is missing. The venue also runs on Base, which is not "
        "collected at all. "
        "Premium is paid out on a call but netted into the collateral on a "
        "put — a 61-strike put on 5 contracts needs 305 USDC, of which the "
        "writer funded 301.45 and the pool contributed 3.55."
    ),
)

HYPERCALL = Protocol(
    key="hypercall",
    name="Hypercall",
    chain="hyperevm",
    otoken_factories=(),
    contracts={
        "Exchange": "0xE6b7FaA216cabb2b38B392f577C9c826378b2240",
        "Factory": "0x2CeeAC2E032e4cCb05FF744619BfAb8cC75efE1b",
        "OptionRegistry": "0x8C8efdd5F78C25215d03c8BbD5D1f6f0F1BB0598",
        "OptionTokenBeacon": "0xfb320352B25Dba7f139EA9Bf8966891CCa12dC35",
        "Router": "0xF41FF41B6F007dE851ACc9F43Bd2f597Da741E2D",
    },
    gamma_compatible=False,
    note=(
        "Custom option-token interface; the Gamma series resolver does not apply. "
        "Historical event and USDC-transfer scans do not measure trading. "
        "As of 20 September 2026, a separate frozen participant study reads "
        "public API fills and portfolios plus on-chain funding evidence. "
        "Individual matches are off-chain according to current architecture docs. "
        "See venues/hypercall/update-2026-09-20; addresses and funding links "
        "do not establish independent human ownership."
    ),
)

PROTOCOLS: dict[str, Protocol] = {p.key: p for p in (RYSK_V12, RYSK_V12_ETHEREUM, HYPERSURFACE, HYPERCALL)}


CHAINS: dict[str, Chain] = {c.key: c for c in (HYPEREVM, ETHEREUM)}


def get_chain(key: str) -> Chain:
    try:
        return CHAINS[key.lower()]
    except KeyError:
        raise KeyError(f"unknown chain {key!r}; known: {sorted(CHAINS)}") from None
