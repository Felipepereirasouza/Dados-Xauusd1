// Busca ticks de um intervalo pelo getRealTimeRates (outro caminho da Dukascopy) e grava CSV no mesmo formato do download histórico.
// Uso: node tempo_real.js DE_ISO ATE_ISO SAIDA.csv
const { getRealTimeRates } = require('dukascopy-node');
const fs = require('fs');
(async () => {
  const [de, ate, saida] = process.argv.slice(2);
  try {
    const cfg = { instrument: 'xauusd', timeframe: 'tick', format: 'array', volumes: true };
    if (de === 'ultimos') cfg.last = 50; else cfg.dates = { from: de, to: ate };
    const dados = await getRealTimeRates(cfg);
    const linhas = ['timestamp,askPrice,bidPrice,askVolume,bidVolume'];
    for (const t of dados) linhas.push(t.join(','));
    fs.writeFileSync(saida, linhas.join('\n') + '\n');
    console.log(`ok ${de} -> ${ate}: ${dados.length} ticks; primeiro ${JSON.stringify(dados[0])}; último ${JSON.stringify(dados[dados.length - 1])}`);
  } catch (e) {
    console.log('erro', e && (e.message || e));
    process.exit(1);
  }
})();
