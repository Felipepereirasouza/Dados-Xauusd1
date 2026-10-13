"""Baixa horas especificas de ticks XAUUSD direto da Dukascopy (arquivos .bi5 por hora),
com retentativas. Entrada: arquivo com 'AAAA-MM-DD HH:00' por linha (UTC).
Saida: dados/patch_ticks.csv (timestamp,askPrice,bidPrice,askVolume,bidVolume) + relatorio."""
import sys, lzma, struct, time, urllib.request, datetime as dt
ent = sys.argv[1]; out = open('dados/patch_ticks.csv', 'w'); rel = open('dados/relatorio_patch.txt', 'w')
out.write('timestamp,askPrice,bidPrice,askVolume,bidVolume\n')
ok = vaz = err = 0
for linha in open(ent):
    linha = linha.strip()
    if not linha: continue
    t = dt.datetime.strptime(linha[:13], '%Y-%m-%d %H').replace(tzinfo=dt.timezone.utc)
    url = f'https://datafeed.dukascopy.com/datafeed/XAUUSD/{t.year}/{t.month-1:02d}/{t.day:02d}/{t.hour:02d}h_ticks.bi5'
    dados = None; ultimo = ''
    for tent in range(8):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30)
            b = r.read()
            if len(b) > 0: dados = b; break
            ultimo = 'vazio'
        except Exception as e:
            ultimo = repr(e)
        time.sleep(2 + 2 * tent)
    if dados is None:
        rel.write(f'{linha}\t{"VAZIO" if ultimo=="vazio" else "ERRO " + ultimo}\n'); vaz += ultimo == 'vazio'; err += ultimo != 'vazio'; continue
    raw = lzma.decompress(dados); base = int(t.timestamp() * 1000); n = len(raw) // 20
    for i in range(n):
        ms, ask, bid, av, bv = struct.unpack('>3I2f', raw[i*20:(i+1)*20])
        out.write(f'{base+ms},{ask/1000:.3f},{bid/1000:.3f},{av:.5f},{bv:.5f}\n')
    rel.write(f'{linha}\tOK {n} ticks\n'); ok += 1
    time.sleep(0.3)
rel.write(f'\nRESUMO ok={ok} vazio={vaz} erro={err}\n'); print(f'ok={ok} vazio={vaz} erro={err}')
