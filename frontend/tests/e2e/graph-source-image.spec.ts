import { expect, test } from '@playwright/test';

for (const entry of ['chat','history']) {
  test(`graph page preview preserves its source pin from ${entry}`, async ({page,context}) => {
    const graph = {root_key:'document:1',product_scope:'fixture-product',site:'factory',direction:'contains'};
    const pin = {version_no:'1',snapshot_id:'fixture-snapshot',source_hash:'a'.repeat(64),
      graph_context:JSON.stringify(graph),graph_hash:'b'.repeat(64)};
    const original = '/api/files/documents/1/original?' + new URLSearchParams(pin).toString();
    const preview = '/api/files/documents/1/pages/1?' + new URLSearchParams({...pin,image_hash:'c'.repeat(64)}).toString();
    const citation = {doc_id:1,page_no:1,file_name:'assembly-fixture.pdf',version_no:1,
      citation_schema:'source-locator-v2',source_id:'D1L0123456789ABCDEF',has_vision:true,
      original_url:original,page_url:preview,locator:{doc_id:'1',version:'1',page:1,row_key:'occurrence-1'},
      snapshot_id:pin.snapshot_id,source_hash:pin.source_hash,graph_context:graph,graph_hash:pin.graph_hash,
      image_hash:'c'.repeat(64)};
    const requests:string[] = [];
    // Every endpoint is intercepted. This proves UI transport, not live RAG acceptance.
    await context.route('**/api/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/api/files/documents/1/pages/1') {
        requests.push(request.url());
        for (const [key,value] of Object.entries({...pin,image_hash:'c'.repeat(64)})) {
          expect(url.searchParams.get(key)).toBe(value);
        }
        return route.fulfill({status:200,contentType:'image/svg+xml',
          body:'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60"><rect width="120" height="60" fill="#ddd"/><text x="10" y="35">Fixture 2D</text></svg>'});
      }
      if (url.pathname === '/api/chat/message' && request.method() === 'POST') {
        return route.fulfill({status:200,contentType:'text/event-stream',body:
          'event: delta\ndata: '+JSON.stringify({text:'Quan hệ cụm đã xác nhận.'})+'\n\n'+
          'event: done\ndata: '+JSON.stringify({citations:[citation]})+'\n\n'});
      }
      let data:unknown = {};
      if (url.pathname === '/api/auth/me') data = {ok:true,user:{user_id:7,username:'fixture-reader',roles:['viewer'],
        allowed_departments:['Technical'],allowed_sites:['factory'],max_security_level:'internal',csrf_token:'fixture-csrf'}};
      else if (url.pathname === '/api/chat/sessions') data = {sessions:entry==='history'?
        [{session_id:'graph-history',cau_hoi:'Quan hệ cụm đã lưu'}]:[]};
      else if (url.pathname === '/api/chat/history' && request.method()==='POST') {
        data = {messages:[{role:'assistant',chat_id:11,content:'Quan hệ cụm đã xác nhận.',citations:[citation]}]};
      } else if (request.method() !== 'GET') return route.abort();
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto('/chat');
    if (entry==='history') {
      await page.getByRole('button',{name:'Lịch sử',exact:true}).click();
      await page.getByRole('button',{name:'Quan hệ cụm đã lưu',exact:true}).click();
    } else {
      await page.locator('textarea').fill('Cụm chứa gì?');
      await page.getByRole('button',{name:'Gửi',exact:true}).click();
    }
    const link = page.getByRole('link',{name:'Mở ảnh nguồn assembly-fixture.pdf, trang 1',exact:true});
    await expect(link).toHaveAttribute('href',preview);
    const image = link.locator('img');
    await expect(image).toBeVisible();
    await expect.poll(()=>image.evaluate(node=>getComputedStyle(node).objectFit)).toBe('contain');
    await expect.poll(()=>image.evaluate(node=>(node as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    const popupPromise = page.waitForEvent('popup');
    await link.click();
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded');
    expect(new URL(popup.url()).pathname).toBe('/api/files/documents/1/pages/1');
    await popup.close();
    expect(requests.length).toBeGreaterThanOrEqual(2);
    await expect(page.getByRole('link',{name:'Tải bản gốc',exact:true})).toHaveAttribute('href',original);
    await page.screenshot({path:`../reports/rag-14-departments-20261005/graph-image-${entry}-final-ui-20261006.png`,fullPage:true});
  });
}
