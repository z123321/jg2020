const express = require('express');
const path = require('path');
const Database = require('better-sqlite3');

const app = express();
const PORT = 3000;

// 1. 连接数据库（只读模式，保护数据）
const db = new Database(path.join(__dirname, 'prices.db'), { readonly: true });

// 2. 静态文件托管：把 index.html 交给 Node.js 托管，彻底消灭跨域问题
app.use(express.static(path.join(__dirname)));

// 3. 搜索接口
app.get('/api/search', (req, res) => {
  const { keyword } = req.query;
  if (!keyword) return res.status(400).json({ error: '缺少 keyword 参数' });

  const min = parseFloat(req.query.min);
  const max = parseFloat(req.query.max);
  const sort = req.query.sort;

  let sql = `
    SELECT title, author, price, condition, seller, shop_url, fetched_at
    FROM prices WHERE keyword = ?
  `;
  const params = [keyword];

  if (!isNaN(min)) { sql += ' AND price >= ?'; params.push(min); }
  if (!isNaN(max)) { sql += ' AND price <= ?'; params.push(max); }

  if (sort === 'price_desc') sql += ' ORDER BY price DESC';
  else if (sort === 'time') sql += ' ORDER BY fetched_at DESC';
  else sql += ' ORDER BY price ASC';

  const items = db.prepare(sql).all(...params);
  res.json({ keyword, count: items.length, items });
});

// 4. 统计接口
app.get('/api/stats', (req, res) => {
  const { keyword } = req.query;
  if (!keyword) return res.status(400).json({ error: '缺少 keyword 参数' });

  const row = db.prepare(`
    SELECT COUNT(*) AS count, MIN(price) AS min_price,
           MAX(price) AS max_price, ROUND(AVG(price),2) AS avg_price
    FROM prices WHERE keyword = ?
  `).get(keyword);

  res.json({ keyword, ...row });
});

app.listen(PORT, () => {
  console.log(`\n=============================`);
  console.log(`🚀 比价工具已启动！`);
  console.log(`👉 请在浏览器打开: http://127.0.0.1:${PORT}/index.html`);
  console.log(`=============================\n`);
});