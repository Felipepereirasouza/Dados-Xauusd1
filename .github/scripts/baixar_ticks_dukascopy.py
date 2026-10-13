#!/usr/bin/env python3
"""
Baixa os ticks do XAUUSD da Dukascopy, UM ARQUIVO POR VEZ (uma hora por arquivo), do mesmo
servidor que a página de exportação usa:
  https://www.dukascopy.com/swiss/english/marketwatch/historical/
  (os dados vêm de https://datafeed.dukascopy.com/datafeed/XAUUSD/AAAA/MM/DD/HHh_ticks.bi5)

Só usa a biblioteca padrão do Python 3 (nada para instalar).

Uso (no Prompt de Comando ou PowerShell, na pasta onde o script está):
  python baixar_ticks_dukascopy.py                       -> jan/2024 até set/2026
  python baixar_ticks_dukascopy.py --inicio 2024-05 --fim 2024-05   -> só um mês (bom para testar)

O que ele faz:
  1. Para cada hora de pregão, baixa o arquivo daquela hora e confere se ele abre (descompacta).
     Se o servidor responder erro (503, 202, 429, rede caiu...), espera e tenta de novo, com
     pausas cada vez maiores (até --tentativas vezes).
  2. Salva cada hora em  <pasta>/bi5/AAAA/MM/DD/HHh_ticks.bi5 . Se rodar de novo, pula o que já baixou.
  3. No fim de cada mês, junta tudo em  <pasta>/csv/xauusd_tick_AAAA-MM.csv
     (timestamp em ms UTC, askPrice, bidPrice, askVolume, bidVolume — o mesmo formato de antes).
  4. Grava  <pasta>/relatorio.txt  com as horas que não vieram, para conferir.

Pode fechar e rodar de novo quando quiser: ele continua de onde parou.
"""
import argparse, lzma, os, random, struct, sys, time, urllib.error, urllib.request
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
BASE = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{a:04d}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
PAGINA = "https://www.dukascopy.com/swiss/english/marketwatch/historical/"
CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36",
    "Referer": PAGINA,
    "Accept": "*/*",
}
PONTO = 1000.0          # XAUUSD: preço inteiro / 1000
MAX_FORA = 0            # 0 = esperar o servidor voltar para sempre; >0 = desistir da hora depois de tantas esperas


# ---------------------------------------------------------------- calendário do pregão
def pascoa(ano):
    a = ano % 19; b = ano // 100; c = ano % 100; d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451; mes = (h + l - 7 * m + 114) // 31; dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def horas_para_baixar(mes):
    """Todas as horas UTC do mês em que pode haver ticks (fim de semana inteiro fica de fora)."""
    ini = datetime.strptime(mes + "-01", "%Y-%m-%d").replace(tzinfo=UTC)
    fim = (ini.replace(day=28) + timedelta(days=4)).replace(day=1)
    h = ini
    while h < fim:
        wd = h.weekday()                       # 0 = segunda ... 6 = domingo
        fora = (wd == 5) or (wd == 4 and h.hour >= 22) or (wd == 6 and h.hour < 20)
        if not fora:
            yield h
        h += timedelta(hours=1)


def deveria_ter_ticks(h):
    """Para o relatório: hora de pregão normal (Nova York: domingo 18h até sexta 17h, sem a pausa 17h-18h)."""
    try:
        from zoneinfo import ZoneInfo
        ny = h.astimezone(ZoneInfo("America/New_York"))
    except Exception:                          # Windows sem tzdata: aproxima com UTC-4
        ny = (h - timedelta(hours=4))
    wd, hr, d = ny.weekday(), ny.hour, ny.date()
    if wd == 5 or (wd == 4 and hr >= 17) or (wd == 6 and hr < 18) or hr == 17: return False
    if (d.month, d.day) in ((12, 25), (1, 1)) or d == pascoa(d.year) - timedelta(days=2): return False
    amanha = d + timedelta(days=1)
    if hr >= 18 and (amanha.month, amanha.day) in ((12, 25), (1, 1)): return False
    return True


# ---------------------------------------------------------------- download de uma hora
def caminho_local(pasta, h):
    return os.path.join(pasta, "bi5", f"{h.year:04d}", f"{h.month:02d}", f"{h.day:02d}", f"{h.hour:02d}h_ticks.bi5")


def arquivo_ok(dados):
    if len(dados) == 0:
        return True                            # hora sem negócio: arquivo vazio é válido
    try:
        raw = lzma.decompress(dados, format=lzma.FORMAT_ALONE)
        return len(raw) % 20 == 0
    except Exception:
        return False


def baixar_hora(h, destino, tentativas, pausa_base, log):
    # o mês na URL da Dukascopy começa em 00 (janeiro = 00)
    url = BASE.format(a=h.year, m=h.month - 1, d=h.day, h=h.hour)
    espera = pausa_base * 4
    ultimo = ""
    k = 0
    fora_do_ar = 0
    while k < tentativas:
        k += 1
        try:
            req = urllib.request.Request(url, headers=CABECALHOS)
            with urllib.request.urlopen(req, timeout=60) as r:
                status = r.status
                dados = r.read()
            if status == 200 and arquivo_ok(dados):
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                tmp = destino + ".parcial"
                with open(tmp, "wb") as f:
                    f.write(dados)
                os.replace(tmp, destino)
                return "ok", len(dados)
            if status != 200:                  # 202 etc.: servidor ocupado, sem arquivo
                motivo_fora = f"status {status}"
            else:
                motivo_fora = None
                ultimo = "status 200 (arquivo corrompido)"
        except urllib.error.HTTPError as e:
            if e.code == 404 and not deveria_ter_ticks(h):   # fim de semana/feriado: não existe arquivo mesmo
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                open(destino, "wb").close()
                return "vazio", 0
            if e.code in (202, 429, 500, 502, 503, 504):
                motivo_fora = f"HTTP {e.code}"    # servidor sobrecarregado/fora do ar
            else:
                motivo_fora = None
                ultimo = f"HTTP {e.code}"         # 404 numa hora de pregão: tenta de novo e, se continuar, avisa
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            motivo_fora = str(e)[:70]             # nem respondeu (conexão recusada, internet caiu)
        except Exception as e:
            motivo_fora = None
            ultimo = type(e).__name__ + ": " + str(e)[:80]
        if motivo_fora:
            # servidor fora do ar: não gasta tentativa — espera e tenta a mesma hora até ele voltar
            k -= 1
            fora_do_ar += 1
            if MAX_FORA and fora_do_ar > MAX_FORA:
                return "falhou", f"servidor fora do ar {MAX_FORA} vezes seguidas ({motivo_fora})"
            espera_ar = min(30 * fora_do_ar, 300)
            log(f"    {h:%Y-%m-%d %H}h: servidor fora do ar ({motivo_fora}); "
                f"esperando {espera_ar}s e tentando de novo (vez {fora_do_ar})")
            time.sleep(espera_ar + random.uniform(0, 2))
            continue
        log(f"    {h:%Y-%m-%d %H}h: tentativa {k}/{tentativas} falhou ({ultimo}); esperando {espera:.0f}s")
        time.sleep(espera + random.uniform(0, 1))
        espera = min(espera * 2, 300)
    return "falhou", ultimo


# ---------------------------------------------------------------- junta o mês em CSV
def montar_csv(pasta, mes):
    os.makedirs(os.path.join(pasta, "csv"), exist_ok=True)
    saida = os.path.join(pasta, "csv", f"xauusd_tick_{mes}.csv")
    n = 0
    with open(saida + ".parcial", "w", newline="") as f:
        f.write("timestamp,askPrice,bidPrice,askVolume,bidVolume\n")
        for h in horas_para_baixar(mes):
            arq = caminho_local(pasta, h)
            if not os.path.exists(arq) or os.path.getsize(arq) == 0:
                continue
            raw = lzma.decompress(open(arq, "rb").read(), format=lzma.FORMAT_ALONE)
            base_ms = int(h.timestamp() * 1000)
            for ms, ask, bid, va, vb in struct.iter_unpack(">IIIff", raw):
                f.write(f"{base_ms + ms},{ask / PONTO:.3f},{bid / PONTO:.3f},{va:.6g},{vb:.6g}\n")
                n += 1
    os.replace(saida + ".parcial", saida)
    return saida, n


# ---------------------------------------------------------------- principal
def meses_entre(inicio, fim):
    a, m = map(int, inicio.split("-")); fa, fm = map(int, fim.split("-"))
    while (a, m) <= (fa, fm):
        yield f"{a:04d}-{m:02d}"
        m += 1
        if m == 13: a, m = a + 1, 1


def main():
    ap = argparse.ArgumentParser(description="Baixa ticks do XAUUSD da Dukascopy, uma hora por vez.")
    ap.add_argument("--inicio", default="2024-01", help="primeiro mês AAAA-MM (padrão 2024-01)")
    ap.add_argument("--fim", default="2026-09", help="último mês AAAA-MM (padrão 2026-09)")
    ap.add_argument("--pasta", default=os.path.join(os.path.expanduser("~"), "Downloads", "ticks_xauusd"),
                    help="onde salvar (padrão: Downloads\\ticks_xauusd)")
    ap.add_argument("--pausa", type=float, default=0.5, help="segundos entre um arquivo e outro (padrão 0,5)")
    ap.add_argument("--tentativas", type=int, default=10, help="tentativas por arquivo (padrão 10)")
    ap.add_argument("--sem-csv", action="store_true", help="não montar os CSVs mensais no fim")
    ap.add_argument("--lista", default="", help="arquivo com horas 'AAAA-MM-DD HH' (UTC): baixa só essas")
    ap.add_argument("--max-fora", type=int, default=0,
                    help="desiste da hora depois de N esperas com o servidor fora do ar (padrão 0 = nunca)")
    a = ap.parse_args()
    global MAX_FORA
    MAX_FORA = a.max_fora

    os.makedirs(a.pasta, exist_ok=True)
    log_arq = open(os.path.join(a.pasta, "log.txt"), "a", encoding="utf-8")
    def log(msg):
        print(msg, flush=True); log_arq.write(msg + "\n"); log_arq.flush()

    if a.lista:
        horas = [datetime.strptime(l.strip()[:13], "%Y-%m-%d %H").replace(tzinfo=UTC)
                 for l in open(a.lista, encoding="utf-8") if l.strip()]
        log(f"\n=== {datetime.now():%Y-%m-%d %H:%M} baixando só as {len(horas)} horas de {a.lista}")
        res_lista = []
        for i, h in enumerate(horas, 1):
            destino = caminho_local(a.pasta, h)
            if os.path.exists(destino):
                res_lista.append(f"{h:%Y-%m-%d %H}h já existia ({os.path.getsize(destino)} bytes)")
                continue
            res, info = baixar_hora(h, destino, a.tentativas, a.pausa, log)
            msg = f"{h:%Y-%m-%d %H}h {res} ({info})"
            log(f"  [{i}/{len(horas)}] {msg}")
            res_lista.append(msg)
            time.sleep(a.pausa)
        with open(os.path.join(a.pasta, "relatorio_lista.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(res_lista) + "\n")
        log("=== fim da lista. Relatório em relatorio_lista.txt")
        return

    log(f"\n=== {datetime.now():%Y-%m-%d %H:%M} início: {a.inicio} até {a.fim}, pasta {a.pasta}")
    relatorio = []
    for mes in meses_entre(a.inicio, a.fim):
        horas = list(horas_para_baixar(mes))
        feitas = vazias = puladas = 0; falhas = []
        t0 = time.time()
        for i, h in enumerate(horas, 1):
            destino = caminho_local(a.pasta, h)
            if os.path.exists(destino):
                puladas += 1
                continue
            res, info = baixar_hora(h, destino, a.tentativas, a.pausa, log)
            if res == "ok": feitas += 1
            elif res == "vazio": vazias += 1
            else:
                falhas.append((h, info)); log(f"    {h:%Y-%m-%d %H}h: DESISTI ({info}) — rode de novo depois")
            if i % 24 == 0:
                print(f"  {mes}: {i}/{len(horas)} horas ({time.time() - t0:.0f}s)", flush=True)
            time.sleep(a.pausa)
        # horas de pregão que ficaram sem ticks
        sem_tick = [h for h in horas if deveria_ter_ticks(h)
                    and (not os.path.exists(caminho_local(a.pasta, h)) or os.path.getsize(caminho_local(a.pasta, h)) == 0)]
        log(f"{mes}: baixadas {feitas}, sem arquivo {vazias}, já existiam {puladas}, falharam {len(falhas)}, "
            f"horas de pregão sem tick {len(sem_tick)}")
        if not a.sem_csv and not falhas:
            saida, n = montar_csv(a.pasta, mes)
            log(f"    CSV: {saida} ({n} ticks)")
        elif falhas:
            log("    CSV não montado porque houve falhas; rode o script de novo para completar este mês.")
        relatorio.append(f"{mes}: falharam {len(falhas)}, horas de pregão sem tick {len(sem_tick)}")
        relatorio += [f"   falhou {h:%Y-%m-%d %H}:00 UTC ({info})" for h, info in falhas]
        relatorio += [f"   sem tick {h:%Y-%m-%d %H}:00 UTC" for h in sem_tick if all(h != f[0] for f in falhas)]
    with open(os.path.join(a.pasta, "relatorio.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(relatorio) + "\n")
    log(f"=== fim. Relatório em {os.path.join(a.pasta, 'relatorio.txt')}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido. Rode de novo para continuar de onde parou.")
