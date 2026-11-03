![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Test Framework](https://img.shields.io/badge/tests-unittest-green.svg)
![License](https://img.shields.io/badge/license-MIT-informational.svg)

# Agendador Cron consciente de DST

Componente determinístico em Python 3.11+ para calcular o próximo instante real de execução de uma expressão cron simplificada, respeitando uma zona IANA como `America/New_York`. Usa somente a biblioteca padrão: `datetime`, `dataclasses`, `zoneinfo` e `unittest`.

## Problema técnico

Somar minutos ou horas diretamente a horários locais não é suficiente para agendamentos que cruzam mudanças de horário de verão. Durante o avanço de DST, algumas horas civis não existem; durante o retorno, algumas horas ocorrem duas vezes. Um scheduler que anexa um fuso sem validar o resultado pode gerar um instante inválido, ignorar uma execução ou escolher o fold errado. Iterar cegamente pelo relógio de parede também pode duplicar ou pular execuções.

O módulo evita essa classe de erro percorrendo datas civis e resolvendo cada horário cron candidato por round-trip entre a hora local e UTC. A comparação de “próximo” é feita entre instantes UTC reais. A busca tem limite de um ciclo gregoriano de 400 anos para que uma combinação de calendário impossível não entre em loop infinito.

## Política determinística para DST

As políticas abaixo são intencionais e fazem parte do contrato do componente:

- **Gap (horário local inexistente):** deslocar o horário para frente pelo tamanho do gap, preservando a posição dentro do intervalo. Exemplo: se o relógio salta de 02:00 para 03:00, a execução cron de 02:30 é projetada para 03:30.
- **Fold (horário local repetido):** considerar as duas ocorrências reais. Retornar a primeira que seja estritamente posterior a `now`. Assim, se a primeira 01:30 já passou, mas a segunda ainda não, a próxima execução é a segunda 01:30.
- **`now` aware:** interpretar como um instante exato e convertê-lo para a zona IANA pedida.
- **`now` naive:** interpretá-lo como horário local na zona pedida, mas rejeitar com `ValueError` se for ambíguo ou inexistente. Para especificar um dos lados do fold, passe um `datetime` aware com o `fold` desejado.

Essas regras evitam que a implementação escolha silenciosamente uma interpretação para o instante atual. Horários cron ambíguos, por outro lado, têm uma política definida: são duas ocorrências válidas e a função retorna a próxima no tempo real.

## Sintaxe cron suportada

O parser aceita cinco campos, nesta ordem:

```text
minuto hora dia-do-mês mês dia-da-semana
```

Suporta inteiros, `*`, listas separadas por vírgula, intervalos `inicio-fim` e passos `/n`. Por exemplo:

```text
*/15 8-17/2 1,15 * 1-5
```

Nomes de meses/dias, segundos e descritores como `@daily` não são suportados. Dia da semana segue a convenção cron: `0` ou `7` é domingo, `1` segunda-feira, até `6` sábado. Quando dia-do-mês e dia-da-semana são ambos restritos, aplica-se a semântica cron tradicional OR; se um dos campos é exatamente `*`, somente o outro restringe a data.

A função pública é:

```python
next_cron_fire(expression: str, now: datetime, zone_key: str) -> datetime
```

Ela retorna um `datetime` aware na zona solicitada, estritamente posterior a `now`. Lança `CronExpressionError` (subclasse de `ValueError`) para expressão inválida, `ValueError` quando `now` naive é ambíguo/inexistente ou quando não há ocorrência no ciclo gregoriano de busca, e `ZoneInfoNotFoundError` para zona IANA desconhecida.

## Exemplos de comportamento

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from cron_dst_scheduler import next_cron_fire

zone = "America/New_York"

# No dia em que 02:30 não existe, projeta para 03:30 local.
now = datetime(2024, 3, 10, 0, 0, tzinfo=ZoneInfo(zone))
print(next_cron_fire("30 2 * * *", now, zone))
# 2024-03-10 03:30:00-04:00

# No fold de outono, se a primeira 01:30 passou, devolve a segunda.
now = datetime(2024, 11, 3, 1, 45, tzinfo=ZoneInfo(zone), fold=0)
print(next_cron_fire("30 1 * * *", now, zone))
# 2024-11-03 01:30:00-05:00 (fold=1)
```

## Testes automatizados

Os testes estão no mesmo arquivo do componente, `cron_dst_scheduler.py`. Não há dependências de terceiros. É necessário Python 3.11+ e uma base IANA que contenha `America/New_York`.

No terminal, execute:

```bash
python cron_dst_scheduler.py
```

Ou, em sistemas nos quais o executável se chama `python3`:

```bash
python3 cron_dst_scheduler.py
```

A suíte usa `unittest` e cobre parsing, validação, semântica de dia da semana, gap de primavera, projeção de 02:30 para 03:30, fold de outono (ambas as ocorrências), `now` naive ambíguo/inexistente, `now` aware em outra zona e garantia de que a ocorrência seja estritamente futura. Para verificar a sintaxe:

```bash
python3 -m py_compile cron_dst_scheduler.py
```
