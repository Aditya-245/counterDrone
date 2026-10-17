const { chromium } = require("playwright")

const PAGES = [
  ["library", "http://127.0.0.1:5173/", "table tbody tr"],
  ["dashboard", "http://127.0.0.1:5173/dashboard", ".stat"],
  ["instructor", "http://127.0.0.1:5173/instructor", "table"],
  ["builder", "http://127.0.0.1:5173/builder", "textarea"],
]

async function shoot(page, name, url, ready) {
  await page.goto(url, { waitUntil: "domcontentloaded" })
  await page.waitForSelector(ready, { timeout: 20000 }).catch(() => {})
  await page.waitForTimeout(600)
  await page.screenshot({ path: `shots/${name}.png`, fullPage: true })
}

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

  for (const [name, url, ready] of PAGES) await shoot(page, name + "-dark", url, ready)

  await page.goto("http://127.0.0.1:5173/", { waitUntil: "domcontentloaded" })
  await page.click('button[aria-label*="theme"]')
  await page.waitForTimeout(300)
  for (const [name, url, ready] of PAGES) await shoot(page, name + "-light", url, ready)

  await browser.close()
  console.log("ok")
})()
