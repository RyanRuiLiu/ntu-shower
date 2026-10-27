"""喷头号按中文数词读，学号逐位读。"""

import pytest

from app.modules.voice import numbers


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "零"),
        (1, "一"),
        (7, "七"),
        (10, "十"),
        (11, "十一"),
        (20, "二十"),
        (99, "九十九"),
        (100, "一百"),
        (101, "一百零一"),
        (105, "一百零五"),
        (110, "一百一十"),
        (111, "一百一十一"),
        (120, "一百二十"),
        (200, "二百"),
        (999, "九百九十九"),
        (1000, "一千"),
        (1001, "一千零一"),
        (1010, "一千零一十"),
        (1100, "一千一百"),
        (10000, "一万"),
        (10001, "一万零一"),
        (10010, "一万零一十"),
    ],
)
def test_cardinal_chars(value: int, expected: str) -> None:
    assert "".join(numbers.cardinal_chars(value)) == expected


def test_cardinal_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="超出"):
        numbers.cardinal_chars(-1)
    with pytest.raises(ValueError, match="超出"):
        numbers.cardinal_chars(100000)


def test_student_id_ignores_trailing_letter() -> None:
    assert "".join(numbers.digit_chars("2530110057A")) == "2530110057"
    assert "".join(numbers.digit_chars("2530110057a")) == "2530110057"
    assert "".join(numbers.digit_chars("2530110057")) == "2530110057"
