const path = require('path');
const fs = require('fs');
const pngToIco = require('png-to-ico');

const pngPath = path.join(__dirname, '..', 'public', 'PurrTypos.png');
const outPath = path.join(__dirname, 'icon.ico');

if (!fs.existsSync(pngPath)) {
  console.error('PNG not found:', pngPath);
  process.exit(1);
}

pngToIco(pngPath)
  .then((buf) => {
    fs.writeFileSync(outPath, buf);
    console.log('Generated build/icon.ico');
  })
  .catch((err) => {
    console.error('png-to-ico failed:', err);
    process.exit(1);
  });
