"""口令哈希。

用 Argon2id（RFC 9106 推荐的密码哈希算法），参数取 argon2-cffi 的默认值：
它按当前硬件给出内存、并行度与迭代次数，比手写一组数字更不容易选错。
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """生成自描述的编码串（含算法、参数与盐）。"""
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """校验口令。哈希串损坏或算法不认识时按失败处理，不抛给调用方。"""
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """参数已落后于当前推荐值时返回 True，认证成功后可就地升级。"""
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


__all__: list[str] = ["hash_password", "needs_rehash", "verify_password"]
