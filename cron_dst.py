"""Agendador cron simplificado, determinístico e consciente de DST.

Sintaxe cron suportada: cinco campos (minuto hora dia-do-mês mês dia-da-semana),
com números, '*', listas, intervalos e passos: por exemplo,
'*/15 8-17/2 1,15 * 1-5'. Nomes de meses/dias e descritores como @daily não são
suportados. Dia da semana segue cron: 0 ou 7 = domingo, 1 = segunda, ..., 6 =
sábado. Quando dia-do-mês e dia-da-semana são ambos restritos, usa-se a regra
cron tradicional OR; se um deles é exatamente '*', só o outro restringe.

Política DST explícita:
- Um horário cron inexistente no gap da primavera é deslocado para frente pelo
tamanho do gap, preservando os minutos (por exemplo, 02:30 -> 03:30 num salto
  de uma hora).
- Um horário cron ambíguo no fold do outono corresponde a duas ocorrências
  reais. A função devolve a primeira ocorrência que ainda seja estritamente
  posterior ao instante atual; uma chamada entre as ocorrências pode, portanto,
  devolver a segunda.
- `now` aware identifica um instante real e é convertido à zona solicitada.
  `now` naive é interpretado como horário local e precisa ser único e válido;
  horários atuais ambíguos ou inexistentes são rejeitados. Para selecionar um
  dos folds de `now`, forneça um datetime aware com `fold` definido.

A busca percorre datas civis e resolve cada horário por round-trip em UTC; não
soma horas no relógio de parede. Dependências: somente biblioteca padrão Python
3.11+ (datetime, dataclasses, zoneinfo, unittest).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_UTC = timezone.utc
_GREGORIAN_CYCLE_DAYS = 146_097  # 400 anos gregorianos


class CronExpressionError(ValueError):
    """Expressão cron malformada ou fora dos limites suportados."""


def _parse_field(
    text: str,
    minimum: int,
    maximum: int,
    field_name: str,
    *,
    sunday_alias: bool = False,
) -> frozenset[int]:
    """Parse um campo cron com listas, intervalos e passos."""
    if not text:
        raise CronExpressionError(f"campo {field_name!r} vazio")

    values: set[int] = set()
    for item in text.split(","):
        if not item:
            raise CronExpressionError(f"item vazio no campo {field_name!r}")

        if item.count("/") > 1:
            raise CronExpressionError(f"passo inválido em {field_name!r}: {item!r}")
        if "/" in item:
            base, step_text = item.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronExpressionError(f"passo inválido em {field_name!r}: {item!r}")
            step = int(step_text)
        else:
            base, step = item, 1

        if base == "*":
            start, end = minimum, maximum
        elif base.count("-") == 1:
            start_text, end_text = base.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise CronExpressionError(f"intervalo inválido em {field_name!r}: {item!r}")
            start, end = int(start_text), int(end_text)
            if start > end:
                raise CronExpressionError(f"intervalo invertido em {field_name!r}: {item!r}")
        else:
            if not base.isdigit():
                raise CronExpressionError(f"valor inválido em {field_name!r}: {item!r}")
            start = int(base)
            # Em cron, `5/10` significa 5 e depois a cada 10 até o limite.
            end = maximum if "/" in item else start

        if start < minimum or end > maximum:
            raise CronExpressionError(
                f"valor fora do intervalo {minimum}-{maximum} no campo {field_name!r}: {item!r}"
            )

        for raw_value in range(start, end + 1, step):
            value = 0 if sunday_alias and raw_value == 7 else raw_value
            values.add(value)

    if not values:
        raise CronExpressionError(f"campo {field_name!r} não seleciona valores")
    return frozenset(values)


@dataclass(frozen=True)
class CronExpression:
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_is_star: bool
    dow_is_star: bool

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        """Parse uma expressão cron de cinco campos."""
        if not isinstance(expression, str):
            raise CronExpressionError("a expressão cron deve ser uma string")
        fields = expression.split()
        if len(fields) != 5:
            raise CronExpressionError(
                "a expressão deve ter cinco campos: minuto hora dia-do-mês mês dia-da-semana"
            )

        minute_text, hour_text, dom_text, month_text, dow_text = fields
        return cls(
            minutes=_parse_field(minute_text, 0, 59, "minuto"),
            hours=_parse_field(hour_text, 0, 23, "hora"),
            days_of_month=_parse_field(dom_text, 1, 31, "dia-do-mês"),
            months=_parse_field(month_text, 1, 12, "mês"),
            days_of_week=_parse_field(
                dow_text, 0, 7, "dia-da-semana", sunday_alias=True
            ),
            dom_is_star=(dom_text == "*"),
            dow_is_star=(dow_text == "*"),
        )

    def matches_date(self, day: date) -> bool:
        """Indique se a data satisfaz mês e regras de dia cron."""
        if day.month not in self.months:
            return False

        dom_matches = day.day in self.days_of_month
        # datetime.weekday(): segunda=0; cron: domingo=0.
        cron_weekday = (day.weekday() + 1) % 7
        dow_matches = cron_weekday in self.days_of_week

        if self.dom_is_star and self.dow_is_star:
            return True
        if self.dom_is_star:
            return dow_matches
        if self.dow_is_star:
            return dom_matches
        return dom_matches or dow_matches


def _possible_instants(wall_time: datetime, zone: ZoneInfo) -> list[datetime]:
    """Return valid aware datetimes for a naive wall time, deduplicated by UTC."""
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        assumed = wall_time.replace(tzinfo=zone, fold=fold)
        instant = assumed.astimezone(_UTC)
        normalized = instant.astimezone(zone)
        if normalized.replace(tzinfo=None) == wall_time:
            candidates[instant] = normalized
    return [candidates[instant] for instant in sorted(candidates)]


def _resolve_scheduled_wall_time(wall_time: datetime, zone: ZoneInfo) -> list[datetime]:
    """Resolve a cron wall time, returning one or two real occurrences.

    For a gap, round-trip both fold assumptions through UTC and select the
    forward projection. This shifts by the transition size, e.g. 02:30 -> 03:30.
    """
    valid = _possible_instants(wall_time, zone)
    if valid:
        return valid  # one normal occurrence, or both occurrences of a fold

    forward_projections: dict[datetime, datetime] = {}
    for fold in (0, 1):
        assumed = wall_time.replace(tzinfo=zone, fold=fold)
        instant = assumed.astimezone(_UTC)
        normalized = instant.astimezone(zone)
        normalized_wall = normalized.replace(tzinfo=None)
        if normalized_wall > wall_time:
            forward_projections[instant] = normalized

    if not forward_projections:
        raise ValueError(
            f"não foi possível projetar o horário inexistente {wall_time!s} em {zone.key}"
        )

    # A projeção para frente preserva a posição dentro do gap; usar o menor
    # horário civil projetado evita saltar mais do que a transição exige.
    return [
        min(
            forward_projections.values(),
            key=lambda value: (value.replace(tzinfo=None), value.astimezone(_UTC)),
        )
    ]


def _resolve_naive_now(now: datetime, zone: ZoneInfo) -> datetime:
    """Interpret naive `now` as local time only when it maps to one instant."""
    if not isinstance(now, datetime):
        raise TypeError("now deve ser um datetime")
    if now.tzinfo is not None and now.utcoffset() is not None:
        return now.astimezone(zone)

    wall_time = now.replace(tzinfo=None)
    valid = _possible_instants(wall_time, zone)
    if not valid:
        raise ValueError(f"now é um horário local inexistente em {zone.key}: {wall_time}")
    if len(valid) > 1:
        raise ValueError(
            f"now é ambíguo em {zone.key}; informe um datetime aware com fold=0 ou fold=1"
        )
    return valid[0]


def next_cron_fire(
    expression: str,
    now: datetime,
    zone_key: str,
) -> datetime:
    """Calcule o próximo instante real de execução, estritamente após `now`.

    Args:
        expression: Cron simplificado com cinco campos.
        now: datetime aware (qualquer zona) ou naive local não ambíguo.
        zone_key: nome IANA, por exemplo ``America/New_York``.

    Returns:
        Próxima ocorrência como datetime aware na zona IANA solicitada.

    Raises:
        CronExpressionError: se a expressão cron for inválida.
        ValueError: se `now` naive for ambíguo/inexistente ou se não houver
            ocorrência dentro de um ciclo gregoriano de 400 anos.
        zoneinfo.ZoneInfoNotFoundError: se a zona IANA não existir no sistema.
    """
    if not isinstance(zone_key, str):
        raise TypeError("zone_key deve ser uma string IANA")
    cron = CronExpression.parse(expression)
    zone = ZoneInfo(zone_key)
    local_now = _resolve_naive_now(now, zone)
    now_utc = local_now.astimezone(_UTC)

    # O calendário gregoriano se repete a cada 400 anos (146097 dias). A busca
    # limitada evita loops infinitos até para combinações sem datas possíveis.
    for day_offset in range(_GREGORIAN_CYCLE_DAYS + 1):
        try:
            civil_day = local_now.date() + timedelta(days=day_offset)
        except OverflowError:
            break

        if not cron.matches_date(civil_day):
            continue

        for hour in sorted(cron.hours):
            for minute in sorted(cron.minutes):
                wall_time = datetime.combine(civil_day, time(hour, minute))
                for occurrence in _resolve_scheduled_wall_time(wall_time, zone):
                    if occurrence.astimezone(_UTC) > now_utc:
                        return occurrence

    raise ValueError(
        "a expressão cron não tem próxima ocorrência no ciclo gregoriano de 400 anos"
    )


# ---------------------------------------------------------------------------
# Testes automatizados: python cron_dst_scheduler.py
# ---------------------------------------------------------------------------
import unittest


class CronExpressionTests(unittest.TestCase):
    def test_parse_lists_ranges_and_steps(self) -> None:
        cron = CronExpression.parse("*/15 8-17/2 1,15 * 1-5")
        self.assertEqual(cron.minutes, frozenset({0, 15, 30, 45}))
        self.assertEqual(cron.hours, frozenset({8, 10, 12, 14, 16}))
        self.assertEqual(cron.days_of_month, frozenset({1, 15}))
        self.assertEqual(cron.days_of_week, frozenset({1, 2, 3, 4, 5}))

    def test_sunday_zero_and_seven_are_equivalent(self) -> None:
        zero = CronExpression.parse("0 0 * * 0")
        seven = CronExpression.parse("0 0 * * 7")
        self.assertEqual(zero.days_of_week, seven.days_of_week)
        self.assertTrue(zero.matches_date(date(2024, 11, 3)))  # domingo

    def test_invalid_expressions_raise(self) -> None:
        invalid = (
            "0 2 * *",          # quatro campos
            "60 2 * * *",       # minuto fora do intervalo
            "0 24 * * *",       # hora fora do intervalo
            "0 2 * 13 *",       # mês fora do intervalo
            "0 2 * * 8",        # weekday fora do intervalo
            "0 2 */0 * *",      # passo zero
            "0 2 10-2 * *",     # intervalo invertido
            "0 2 1,,2 * *",     # item vazio
        )
        for expression in invalid:
            with self.subTest(expression=expression):
                with self.assertRaises(CronExpressionError):
                    CronExpression.parse(expression)

    def test_non_string_expression_is_rejected(self) -> None:
        with self.assertRaises(CronExpressionError):
            CronExpression.parse(None)  # type: ignore[arg-type]

    def test_dom_and_dow_use_cron_or_semantics(self) -> None:
        # Dom=1 ou segunda-feira: em 2 jan 2024, a próxima ocorrência é segunda 8 jan.
        now = datetime(2024, 1, 2, 10, 0, tzinfo=ZoneInfo("UTC"))
        result = next_cron_fire("0 9 1 * 1", now, "America/New_York")
        self.assertEqual(result.date(), date(2024, 1, 8))
        self.assertEqual((result.hour, result.minute), (9, 0))


class CronDstSchedulingTests(unittest.TestCase):
    ZONE = "America/New_York"

    def test_spring_gap_shifts_forward_by_gap_size(self) -> None:
        now = datetime(2024, 3, 10, 0, 0, tzinfo=ZoneInfo(self.ZONE))
        result = next_cron_fire("30 2 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-03-10T03:30:00-04:00")
        self.assertEqual(result.astimezone(_UTC), datetime(2024, 3, 10, 7, 30, tzinfo=_UTC))

    def test_gap_projection_already_passed_moves_to_next_day(self) -> None:
        now = datetime(2024, 3, 10, 3, 45, tzinfo=ZoneInfo(self.ZONE))
        result = next_cron_fire("30 2 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-03-11T02:30:00-04:00")

    def test_fold_returns_first_occurrence_when_both_are_future(self) -> None:
        now = datetime(2024, 11, 3, 0, 30, tzinfo=ZoneInfo(self.ZONE))
        result = next_cron_fire("30 1 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-11-03T01:30:00-04:00")
        self.assertEqual(result.fold, 0)

    def test_fold_returns_second_occurrence_if_first_has_passed(self) -> None:
        now = datetime(2024, 11, 3, 1, 45, tzinfo=ZoneInfo(self.ZONE), fold=0)
        result = next_cron_fire("30 1 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-11-03T01:30:00-05:00")
        self.assertEqual(result.fold, 1)

    def test_fold_moves_to_next_day_after_both_occurrences(self) -> None:
        now = datetime(2024, 11, 3, 1, 45, tzinfo=ZoneInfo(self.ZONE), fold=1)
        result = next_cron_fire("30 1 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-11-04T01:30:00-05:00")

    def test_naive_ambiguous_now_is_rejected(self) -> None:
        now = datetime(2024, 11, 3, 1, 30)
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            next_cron_fire("0 2 * * *", now, self.ZONE)

    def test_naive_nonexistent_now_is_rejected(self) -> None:
        now = datetime(2024, 3, 10, 2, 30)
        with self.assertRaisesRegex(ValueError, "inexistente"):
            next_cron_fire("0 3 * * *", now, self.ZONE)

    def test_non_datetime_now_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "datetime"):
            next_cron_fire("0 3 * * *", "2024-03-10T00:00", self.ZONE)  # type: ignore[arg-type]

    def test_aware_now_in_another_zone_is_converted_by_instant(self) -> None:
        now_utc = datetime(2024, 11, 3, 5, 45, tzinfo=_UTC)
        result = next_cron_fire("30 1 * * *", now_utc, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-11-03T01:30:00-05:00")
        self.assertEqual(result.fold, 1)

    def test_occurrence_equal_to_now_is_not_returned(self) -> None:
        now = datetime(2024, 3, 11, 2, 30, tzinfo=ZoneInfo(self.ZONE))
        result = next_cron_fire("30 2 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-03-12T02:30:00-04:00")

    def test_weekday_schedule_skips_weekend(self) -> None:
        now = datetime(2024, 3, 8, 10, 0, tzinfo=ZoneInfo(self.ZONE))  # sexta-feira
        result = next_cron_fire("0 9 * * 1-5", now, self.ZONE)
        self.assertEqual(result.date(), date(2024, 3, 11))
        self.assertEqual((result.hour, result.minute), (9, 0))


if __name__ == "__main__":
    unittest.main()
