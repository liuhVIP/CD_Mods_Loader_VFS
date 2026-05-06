"""独立加载器数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PazEntry:
    """PAMT 中的一条游戏文件索引记录。"""

    path: str
    paz_file: str
    offset: int
    comp_size: int
    orig_size: int
    flags: int
    paz_index: int
    encrypted_override: bool | None = field(default=None, compare=False)

    @property
    def compressed(self) -> bool:
        """是否在 PAZ 中以压缩形态存储。"""
        return self.comp_size != self.orig_size

    @property
    def compression_type(self) -> int:
        """返回压缩类型：0=原始，1=DDS 特殊，2=LZ4。"""
        return (self.flags >> 16) & 0x0F

    @property
    def encrypted(self) -> bool:
        """根据文件后缀和运行时探测结果判断是否需要 ChaCha20。"""
        if self.encrypted_override is not None:
            return self.encrypted_override
        return self.path.lower().endswith((".xml", ".css", ".html", ".js"))

    def with_encrypted_override(self, value: bool) -> "PazEntry":
        """返回带加密探测覆盖值的新对象，保持 dataclass 不可变语义。"""
        return PazEntry(
            path=self.path,
            paz_file=self.paz_file,
            offset=self.offset,
            comp_size=self.comp_size,
            orig_size=self.orig_size,
            flags=self.flags,
            paz_index=self.paz_index,
            encrypted_override=value,
        )


@dataclass(frozen=True)
class DiscoveredMod:
    """扫描到的单个可处理模组。"""

    name: str
    path: Path
    mod_type: str
    fingerprint: str


@dataclass
class PatchPlan:
    """JSON 补丁聚合后的单文件计划。"""

    game_file: str
    changes: list[dict]
    signature: str | None = None
    source_mods: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OverlayInputEntry:
    """准备写入 overlay 的解压后文件内容与元数据。"""

    content: bytes
    entry_path: str
    pamt_dir: str
    compression_type: int
    encrypted: bool = False
    crypto_filename: str | None = None


@dataclass(frozen=True)
class BuiltOverlayEntry:
    """已写入 overlay PAZ 后的 PAMT 记录源数据。"""

    entry_path: str
    dir_path: str
    filename: str
    paz_offset: int
    comp_size: int
    decomp_size: int
    flags: int
    content: bytes = field(default=b"", compare=False)
    dds_m_values: tuple[int, int, int, int] | None = None
    dds_last4: int = 0


@dataclass(frozen=True)
class OverlayBuildResult:
    """overlay 构建结果。"""

    overlay_dir: str
    paz_bytes: bytes
    pamt_bytes: bytes
    entries: list[BuiltOverlayEntry]


@dataclass(frozen=True)
class LoaderResult:
    """命令执行结果摘要。"""

    overlay_dir: str | None
    loaded_mods: list[DiscoveredMod]
    warnings: list[str]
    errors: list[str]
