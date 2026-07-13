async (page) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };

  const textPreview = (value) => value.replace(/\s+/g, ' ').trim().slice(0, 1000);

  const expectText = async (locator, text, label, timeoutMs = 10000) => {
    const deadline = Date.now() + timeoutMs;
    let lastText = '';
    let lastError = null;

    while (Date.now() < deadline) {
      try {
        lastText = await locator.innerText({ timeout: 1000 });
        if (lastText.includes(text)) return;
      } catch (error) {
        lastError = error;
      }
      await page.waitForTimeout(100);
    }

    const diagnostic = lastError
      ? `Last error: ${lastError.message}`
      : `Last text: ${textPreview(lastText)}`;
    throw new Error(`${label} missing ${text} after ${timeoutMs}ms. ${diagnostic}`);
  };

  await page.goto('http://127.0.0.1:8765/', { waitUntil: 'domcontentloaded' });

  const memoryNav = page.locator("button.nav-item[data-view='memory']");
  await memoryNav.waitFor({ state: 'visible', timeout: 10000 });
  await assert((await memoryNav.isEnabled()), 'memory nav button is not enabled');

  await memoryNav.click();

  const memoryView = page.locator('#view-memory');
  await memoryView.waitFor({ state: 'attached', timeout: 10000 });
  await page.waitForFunction(() => {
    const view = document.querySelector('#view-memory');
    if (!view || !view.classList.contains('active')) return false;
    const style = window.getComputedStyle(view);
    return style.display !== 'none' && style.visibility !== 'hidden';
  }, null, { timeout: 10000 });
  await assert((await memoryView.isVisible()), 'memory view is not visible after navigation');
  await page.locator('#memory-layer-board [data-memory-id]').first().waitFor({ state: 'visible', timeout: 10000 });

  for (const text of [
    '记忆层管理',
    '假设账本',
    '代理映射',
    'Kill 条件',
    'WQB 动作队列',
    '对抗审查',
    'RAG Trace',
  ]) {
    await expectText(memoryView, text, 'memory view');
  }

  await page.locator('#promote-memory-button').click();
  await expectText(page.locator('#inspector-copy'), '已标记晋升', 'promote action');

  await page.locator("button[data-lang='en']").click();
  await expectText(memoryView, 'Hypothesis Ledger', 'english memory view');
  await expectText(memoryView, 'WQB Action Lanes', 'english memory view');

  return 'MEMORY_PHASE1_E2E_OK';
}
