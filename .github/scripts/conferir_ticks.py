#!/usr/bin/env python3
"""Confere um CSV de ticks do dukascopy-node e rebaixa os dias que têm horas de pregão sem nenhum tick.

Uso:
  python3 conferir_ticks.py ARQUIVO.csv AAAA-MM [--passes 3] [--relatorio lacunas.txt]

O CSV é o formato do dukascopy-node com -v true: timestamp(ms UTC),askPrice,bidPrice,askVolume,bidVolume.

Pregão considerado (horário de Nova York): domingo 18:00 até sexta 17:00, sem a pausa diária 17:00–18:00,
fechado em 25/12, 01/01 e na Sexta-Feira Santa. Uma hora "falta" quando não tem nenhum tick.

Para cada dia com hora faltando, roda de novo o dukascopy-node só para aquele dia (com novas tentativas),
acrescenta só os ticks das horas que estavam faltando e confere de novo. Repete até --passes vezes.
O comando de download pode ser trocado pela variável DUKA_CMD (usada nos testes).
"""
import argparse, csv, os, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
DUKA_VERSAO = os.environ.get("DUKA_VERSAO", "1.50.0")


def pascoa(ano):
    a = ano % 19; b = ano // 100; c = ano % 100; d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451; mes = (h + l - 7 * m + 114) // 31; dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def hora_de_pregao(h_utc):
    """h_utc: datetime UTC no início da hora. True se o ouro deveria ter ticks nessa hora."""
    ny = h_utc.astimezone(NY)
    wd, hr, d = ny.weekday(), ny.hour, ny.date()
    if wd == 5: return False                      # sábado
    if wd == 4 and hr >= 17: return False         # sexta depois do fechamento
    if wd == 6 and hr < 18: return False          # domingo antes da abertura
    if hr == 17: return False                     # pausa diária
    if (d.month, d.day) in ((12, 25), (1, 1)): return False
    if d == pascoa(d.year) - timedelta(days=2): return False   # Sexta-Feira Santa
    # a sessão que abre às 18h da véspera de 25/12 e 01/01 também é fechada
    amanha = d + timedelta(days=1)
    if hr >= 18 and (amanha.month, amanha.day) in ((12, 25), (1, 1)): return False
    return True


def horas_do_mes(mes):
    ini = datetime.strptime(mes + "-01", "%Y-%m-%d").replace(tzinfo=UTC)
    fim = (ini.replace(day=28) + timedelta(days=4)).replace(day=1)
    h = ini
    while h < fim:
        yield h
        h += timedelta(hours=1)


def ler(caminho):
    with open(caminho, newline="") as f:
        r = csv.reader(f)
        cab = next(r)
        linhas = [l for l in r if l]
    return cab, linhas


def faltando(linhas, mes):
    tem = {int(l[0]) // 3_600_000 for l in linhas}
    out = []
    for h in horas_do_mes(mes):
        if hora_de_pregao(h) and int(h.timestamp()) // 3600 not in tem:
            out.append(h)
    return out


def baixar_dia(dia, pasta):
    """Baixa um dia inteiro (UTC) e devolve as linhas."""
    de = dia.strftime("%Y-%m-%d"); ate = (dia + timedelta(days=1)).strftime("%Y-%m-%d")
    nome = "dia_" + dia.strftime("%Y%m%d")
    cmd = os.environ.get("DUKA_CMD")
    if cmd:
        args = cmd.split() + [de, ate, pasta, nome]
    else:
        args = ["npx", "-y", f"dukascopy-node@{DUKA_VERSAO}", "-i", "xauusd", "-from", de, "-to", ate,
                "-t", "tick", "-f", "csv", "-v", "true", "-fl", "false",
                *os.environ.get("DUKA_OPCOES", "-r 6 -rp 3000").split(),
                "-dir", pasta, "-fn", nome]
    print("  baixando de novo", de, flush=True)
    res = subprocess.run(args, capture_output=True, text=True)
    arq = os.path.join(pasta, nome + ".csv")
    if res.returncode != 0 or not os.path.exists(arq):
        print("  falhou:", (res.stderr or res.stdout)[-400:], flush=True)
        return []
    _, linhas = ler(arq)
    return linhas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv"); ap.add_argument("mes")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--relatorio", default="")
    a = ap.parse_args()

    cab, linhas = ler(a.csv)
    falta = faltando(linhas, a.mes)
    print(f"{a.mes}: {len(linhas)} ticks, {len(falta)} horas de pregão sem tick", flush=True)
    tmp = tempfile.mkdtemp()
    for p in range(1, a.passes + 1):
        if not falta: break
        dias = sorted({h.date() for h in falta})
        print(f"passada {p}: {len(dias)} dias para baixar de novo", flush=True)
        novas = []
        for d in dias:
            novas += baixar_dia(datetime(d.year, d.month, d.day, tzinfo=UTC), tmp)
        # junta sem duplicar (mesmo timestamp e mesmos preços) e mantém só o mês
        ini_ms = int(datetime.strptime(a.mes + "-01", "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
        fim_dt = (datetime.strptime(a.mes + "-01", "%Y-%m-%d").replace(day=28) + timedelta(days=4)).replace(day=1, tzinfo=UTC)
        fim_ms = int(fim_dt.timestamp() * 1000)
        # só entram ticks das horas que estavam faltando (as horas que já existiam ficam como estavam)
        horas_falta = {int(h.timestamp()) // 3600 for h in falta}
        vistos = set()
        for l in novas:
            t = int(l[0])
            if ini_ms <= t < fim_ms and t // 3_600_000 in horas_falta and tuple(l) not in vistos:
                linhas.append(l); vistos.add(tuple(l))
        linhas.sort(key=lambda l: int(l[0]))
        antes = len(falta)
        falta = faltando(linhas, a.mes)
        print(f"  depois da passada {p}: {len(falta)} horas faltando (eram {antes})", flush=True)

    with open(a.csv, "w", newline="") as f:
        w = csv.writer(f); w.writerow(cab); w.writerows(linhas)

    txt = [f"{a.mes}: {len(linhas)} ticks; horas de pregão sem tick depois da conferência: {len(falta)}"]
    txt += [h.strftime("%Y-%m-%d %H:00 UTC") for h in falta]
    if a.relatorio:
        with open(a.relatorio, "w") as f: f.write("\n".join(txt) + "\n")
    print("\n".join(txt))
    if falta:
        print(f"::warning title=Horas sem tick em {a.mes}::{len(falta)} horas de pregão continuam sem tick "
              f"depois de {a.passes} passadas (lista no arquivo de lacunas). Podem ser feriados com pregão "
              f"reduzido ou falha persistente da Dukascopy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
