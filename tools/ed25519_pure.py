"""Ed25519 (RFC 8032, раздел 6) на чистом Python без зависимостей: подпись списка в CI и проверка в программе.
Не рассчитано на защиту от атак по времени: подписывает только CI с секретом, проверка работает с открытыми данными."""
import hashlib

P = 2 ** 255 - 19
Q = 2 ** 252 + 27742317777372353535851937790883648493


def _sha512(s):
    return hashlib.sha512(s).digest()


def _inv(x):
    return pow(x, P - 2, P)


D = -121665 * _inv(121666) % P


def _sha512_modq(s):
    return int.from_bytes(_sha512(s), "little") % Q


def _add(p1, p2):
    a = (p1[1] - p1[0]) * (p2[1] - p2[0]) % P
    b = (p1[1] + p1[0]) * (p2[1] + p2[0]) % P
    c = 2 * p1[3] * p2[3] * D % P
    d = 2 * p1[2] * p2[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f, g * h, f * g, e * h)


def _mul(s, pt):
    r = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        s >>= 1
    return r


def _equal(p1, p2):
    if (p1[0] * p2[2] - p2[0] * p1[2]) % P != 0:
        return False
    return (p1[1] * p2[2] - p2[1] * p1[2]) % P == 0


_SQRT_M1 = pow(2, (P - 1) // 4, P)


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


_GY = 4 * _inv(5) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)


def _compress(pt):
    zinv = _inv(pt[2])
    x = pt[0] * zinv % P
    y = pt[1] * zinv % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _expand(secret):
    if len(secret) != 32:
        raise ValueError("секретный ключ: 32 байта")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


def public_key(secret):
    a, _ = _expand(secret)
    return _compress(_mul(a, G))


def sign(secret, msg):
    a, prefix = _expand(secret)
    pub = _compress(_mul(a, G))
    r = _sha512_modq(prefix + msg)
    rs = _compress(_mul(r, G))
    h = _sha512_modq(rs + pub + msg)
    s = (r + h * a) % Q
    return rs + int.to_bytes(s, 32, "little")


def verify(public, msg, signature):
    """True только для верной подписи; любые ошибки формата дают False, а не исключение."""
    try:
        if len(public) != 32 or len(signature) != 64:
            return False
        a_pt = _decompress(public)
        if not a_pt:
            return False
        rs = signature[:32]
        r_pt = _decompress(rs)
        if not r_pt:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= Q:
            return False
        h = _sha512_modq(rs + public + msg)
        return _equal(_mul(s, G), _add(r_pt, _mul(h, a_pt)))
    except Exception:
        return False
