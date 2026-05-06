"""PAZ 解密与 LZ4 压缩工具。"""

from __future__ import annotations

import os
import struct

from cdmm.common.hashlittle import hashlittle

try:
    import cdumm_native as _native
except ImportError:
    _native = None

# ChaCha20 文件名派生密钥的固定参数。
HASH_INITVAL = 0x000C5EDE
IV_XOR = 0x60616263
XOR_DELTAS = [
    0x00000000,
    0x0A0A0A0A,
    0x0C0C0C0C,
    0x06060606,
    0x0E0E0E0E,
    0x0A0A0A0A,
    0x06060606,
    0x02020202,
]

# LZ4 block 格式单块输入上限。
LZ4_MAX_INPUT_SIZE = 0x7E000000


def derive_key_iv(filename: str) -> tuple[bytes, bytes]:
    """根据文件名派生 ChaCha20 key/iv。"""
    if _native is not None:
        return _native.derive_key_iv(filename)
    basename = os.path.basename(filename).lower()
    seed = hashlittle(basename.encode("utf-8"), HASH_INITVAL)
    iv = struct.pack("<I", seed) * 4
    key_base = seed ^ IV_XOR
    key = b"".join(struct.pack("<I", key_base ^ delta) for delta in XOR_DELTAS)
    return key, iv


def decrypt(data: bytes, filename: str) -> bytes:
    """使用文件名派生密钥解密数据。"""
    if _native is not None:
        return _native.chacha20_decrypt(data, filename)
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

    key, iv = derive_key_iv(filename)
    cipher = Cipher(algorithms.ChaCha20(key, iv), mode=None)
    return cipher.encryptor().update(data)


def encrypt(data: bytes, filename: str) -> bytes:
    """ChaCha20 加密与解密为同一操作。"""
    return decrypt(data, filename)


def lz4_decompress(data: bytes, original_size: int) -> bytes:
    """解压无 frame header 的 LZ4 block。"""
    if _native is not None:
        return _native.lz4_decompress(data, original_size)
    import lz4.block

    return lz4.block.decompress(data, uncompressed_size=original_size)


def lz4_compress(data: bytes) -> bytes:
    """压缩为游戏使用的无 frame header LZ4 block。"""
    if len(data) > LZ4_MAX_INPUT_SIZE:
        raise ValueError(
            f"LZ4 输入过大：{len(data):,} 字节，超过单块上限 {LZ4_MAX_INPUT_SIZE:,}"
        )
    if _native is not None:
        return _native.lz4_compress(data)
    import lz4.block

    return lz4.block.compress(data, store_size=False)
