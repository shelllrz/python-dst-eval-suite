"""Caso 8 — Duração transcorrida, DST e horários locais ambíguos/inexistentes.

ENUNCIADO DO TESTE
------------------
Escreva Python 3.11+ usando apenas a biblioteca padrão para implementar
`hours_until_5pm_tomorrow(local_now, zone_key, fold=None)`. `local_now` é um
datetime sem tzinfo contendo a hora de parede local na zona IANA `zone_key`;
`fold` é opcional. Retorne um float com o número de horas reais transcorridas
até às 17:00 do próximo dia civil naquela zona. Não presuma que todo horário
local existe ou é único: rejeite um horário inexistente com ValueError e exija
`fold=0` ou `fold=1` para um horário ambíguo. Rejeite datetime com tzinfo,
valores de fold inválidos e targets de amanhã às 17:00 que sejam ambíguos ou
inexistentes. Inclua solução executável e teste os casos especificados.

FIXTURES PRINCIPAIS (America/New_York)
--------------------------------------
- 2024-03-09 23:00, sem fold -> 17.0 horas (avanço de DST).
- 2024-11-02 23:00, sem fold -> 19.0 horas (retorno de DST).
- 2024-11-03 01:30, fold=0 -> 40.5 horas.
- 2024-11-03 01:30, fold=1 -> 39.5 horas.
- 2024-03-10 03:30, sem fold -> 37.5 horas.
- Rejeitar 2024-03-10 02:30 (horário inexistente).
- Rejeitar 2024-11-03 01:30 sem fold (horário ambíguo).

O teste deve medir duração entre instantes reais, e não subtrair diretamente
horários locais ingênuos. `fold=0` seleciona a primeira ocorrência de um horário
repetido; `fold=1`, a segunda. Dependências: CPython 3.11+ e base de fusos IANA
com a zona America/New_York disponível.
"""

from __future__ import annotations

import unittest
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_UTC = timezone.utc


def _localize_wall(
    wall: datetime,
    zone: ZoneInfo,
    fold: int | None = None,
) -> datetime:
    """Converta um horário local ingênuo, rejeitando gap/fold não resolvidos."""
    if not isinstance(wall, datetime):
        raise TypeError("wall must be a datetime")
    if wall.tzinfo is not None:
        raise ValueError("wall must be a naive local datetime")
    if fold is not None and (type(fold) is not int or fold not in (0, 1)):
        raise ValueError("fold must be None, 0, or 1")

    # zoneinfo permite construir datetimes locais inexistentes. Round-trip por
    # UTC identifica gaps: a hora local não volta ao mesmo wall time.
    valid: dict[int, datetime] = {}
    for candidate_fold in (0, 1):
        candidate = wall.replace(tzinfo=zone, fold=candidate_fold)
        round_trip = candidate.astimezone(_UTC).astimezone(zone)
        if round_trip.replace(tzinfo=None) == wall:
            valid[candidate_fold] = candidate

    if not valid:
        raise ValueError("nonexistent local time")

    instants = {candidate.astimezone(_UTC) for candidate in valid.values()}
    if len(instants) == 2:
        if fold is None:
            raise ValueError("ambiguous local time; specify fold=0 or fold=1")
        return valid[fold]

    if fold is not None:
        raise ValueError("fold is only valid for an ambiguous local time")
    return valid[0]


def hours_until_5pm_tomorrow(
    local_now: datetime,
    zone_key: str,
    fold: int | None = None,
) -> float:
    """Retorne as horas reais até 17:00 do próximo dia civil local.

    `local_now` deve ser um datetime ingênuo com hora de parede local. Horários
    inexistentes são rejeitados; horários ambíguos exigem fold explícito.
    """
    if not isinstance(local_now, datetime):
        raise TypeError("local_now must be a datetime")
    if local_now.tzinfo is not None:
        raise ValueError("local_now must be a naive local datetime")

    zone = ZoneInfo(zone_key)
    now = _localize_wall(local_now, zone, fold)

    target_wall = datetime.combine(
        local_now.date() + timedelta(days=1),
        time(17, 0),
    )
    # Com fold=None, helper também rejeita target ambíguo ou inexistente.
    target = _localize_wall(target_wall, zone)

    elapsed = target.astimezone(_UTC) - now.astimezone(_UTC)
    return elapsed.total_seconds() / 3600


class HoursUntil5pmTomorrowTests(unittest.TestCase):
    def test_spring_forward_changes_elapsed_duration(self) -> None:
        self.assertEqual(
            hours_until_5pm_tomorrow(datetime(2024, 3, 9, 23), "America/New_York"),
            17.0,
        )

    def test_fall_back_changes_elapsed_duration(self) -> None:
        self.assertEqual(
            hours_until_5pm_tomorrow(datetime(2024, 11, 2, 23), "America/New_York"),
            19.0,
        )

    def test_ambiguous_wall_time_requires_fold(self) -> None:
        wall = datetime(2024, 11, 3, 1, 30)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            hours_until_5pm_tomorrow(wall, "America/New_York")

    def test_fold_selects_distinct_real_instants(self) -> None:
        wall = datetime(2024, 11, 3, 1, 30)
        first = hours_until_5pm_tomorrow(wall, "America/New_York", fold=0)
        second = hours_until_5pm_tomorrow(wall, "America/New_York", fold=1)
        self.assertEqual(first, 40.5)
        self.assertEqual(second, 39.5)
        self.assertNotEqual(first, second)

    def test_nonexistent_wall_time_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonexistent"):
            hours_until_5pm_tomorrow(
                datetime(2024, 3, 10, 2, 30), "America/New_York"
            )

    def test_normal_wall_time(self) -> None:
        self.assertEqual(
            hours_until_5pm_tomorrow(datetime(2024, 3, 10, 3, 30), "America/New_York"),
            37.5,
        )

    def test_aware_datetime_is_rejected(self) -> None:
        aware = datetime(2024, 3, 9, 23, tzinfo=ZoneInfo("America/New_York"))
        with self.assertRaisesRegex(ValueError, "naive"):
            hours_until_5pm_tomorrow(aware, "America/New_York")

    def test_invalid_fold_is_rejected(self) -> None:
        wall = datetime(2024, 3, 9, 23)
        for invalid_fold in (2, -1, True, "0"):
            with self.subTest(fold=invalid_fold):
                with self.assertRaisesRegex(ValueError, "fold"):
                    hours_until_5pm_tomorrow(
                        wall, "America/New_York", fold=invalid_fold  # type: ignore[arg-type]
                    )

    def test_fold_is_rejected_for_unambiguous_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "only valid for an ambiguous"):
            hours_until_5pm_tomorrow(
                datetime(2024, 3, 9, 23), "America/New_York", fold=0
            )


if __name__ == "__main__":
    unittest.main()
