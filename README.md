![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Test Framework](https://img.shields.io/badge/tests-unittest-green.svg)
![License](https://img.shields.io/badge/license-MIT-informational.svg)

# Caso 8 — Duração transcorrida e horário de verão em Python

Este desafio avalia se uma implementação Python calcula **tempo realmente transcorrido** quando o intervalo cruza uma transição de horário de verão (DST), e se trata corretamente horários locais ambíguos ou inexistentes.

## Falha técnica avaliada

Uma implementação aparentemente plausível pode representar os horários locais como `datetime` ingênuos, sem fuso, e subtrair diretamente os valores do relógio. Isso confunde a diferença entre horários de parede com a duração entre instantes reais. Quando o relógio avança ou retrocede por DST, as duas quantidades podem divergir em uma hora.

O estudo de bugs de data e hora em software Python documenta um exemplo atribuído ao ChatGPT-4o: para Nova York, em **9 de março de 2024 às 23:00**, calcular o tempo até **17:00 do dia seguinte** retornou 18 horas, embora a duração correta seja **17 horas**, devido ao avanço do relógio naquela noite. A solução precisa considerar a zona IANA, interpretar a meta como 17:00 do próximo dia civil local e calcular a diferença entre os instantes correspondentes, por exemplo convertendo ambos para UTC antes da subtração. O estudo também discute problemas com horários locais ambíguos e inexistentes. [Fonte: *It’s About Time: An Empirical Study of Date and Time Bugs in Open-Source Python Software*](https://rohan.padhye.org/files/datetimebugs-msr25.pdf).

Um horário ambíguo ocorre quando o relógio repete uma hora durante o retorno ao horário padrão. Em Python, `fold=0` e `fold=1` distinguem as duas ocorrências. Um horário inexistente ocorre quando o relógio salta à frente; o horário de parede correspondente nunca ocorreu. A implementação do desafio deve rejeitar o horário inexistente e exigir `fold` explícito para um horário ambíguo, em vez de aceitar silenciosamente uma interpretação implícita.

## Enunciado do teste

Implemente em Python 3.11 ou superior, usando apenas a biblioteca padrão, a função `hours_until_5pm_tomorrow(local_now, zone_key, fold=None)`. `local_now` é um `datetime` sem `tzinfo` contendo a hora de parede local na zona IANA `zone_key`; `fold` é opcional. Retorne um `float` com o número de horas reais transcorridas até às 17:00 do próximo dia civil naquela zona. Rejeite horários locais inexistentes com `ValueError` e exija `fold=0` ou `fold=1` para horários ambíguos. Rejeite `datetime` com `tzinfo`, valores de `fold` inválidos e metas de amanhã às 17:00 que sejam ambíguas ou inexistentes.

As fixtures automatizadas usam `America/New_York` e cobrem:

- 2024-03-09 23:00, antes do avanço de DST: resultado 17 horas.
- 2024-11-02 23:00, antes do retorno de DST: resultado 19 horas.
- 2024-11-03 01:30 repetido: `fold=0` resulta em 40,5 horas e `fold=1` em 39,5 horas.
- 2024-03-10 03:30, após o avanço: resultado 37,5 horas.
- Horário inexistente (2024-03-10 02:30) e horário ambíguo sem `fold`: ambos devem ser rejeitados.

## Instalação e execução

### 1. Clonar o repositório

```bash
git clone [https://github.com/shelllrz/python-dst-eval-suite.git](https://github.com/shelllrz/python-dst-eval-suite.git)
cd python-dst-eval-suite