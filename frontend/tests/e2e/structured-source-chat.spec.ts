import { expect, test } from '@playwright/test';

test('chat keeps sheet citations distinct and opens approved rows without decimal loss', async ({page}) => {
  const pages:number[] = [];
  const citations = ['B2','B3'].map((cell,index)=>({doc_id:17,page_no:null,file_name:'ledger.xlsx',
    version_no:3,has_vision:false,original_url:'/api/files/documents/17/original',source_id:`D17L${index}`,
    citation_schema:'source-locator-v2',scope_id:index?'b':'a',snapshot_id:'s1',scope_hash:'b'.repeat(64),
    locator:{doc_id:'17',version:'3',sheet:'Sổ',cell,row_key:`row-${index+2}`}}));
  // Transport fixture only: every API call is intercepted, including the chat POST.
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/chat/message' && request.method() === 'POST') {
      return route.fulfill({status:200,contentType:'text/event-stream',body:
        'event: delta\ndata: '+JSON.stringify({text:'Tổng A: 4,00 VND. Tổng B: 8,00 VND. Chênh lệch: 4,00 VND. Tỷ lệ: 100,00%.'})+'\n\n'+
        'event: done\ndata: '+JSON.stringify({citations})+'\n\n'});
    }
    if (request.method() !== 'GET') return route.abort();
    let data:unknown = {};
    if (url.pathname === '/api/auth/me') data = {ok:true,user:{user_id:7,username:'offline-reader',roles:['viewer'],
      allowed_departments:['Accountant'],allowed_sites:['HQ'],max_security_level:'internal',csrf_token:'offline-csrf'}};
    else if (url.pathname === '/api/chat/sessions') data = {sessions:[]};
    else if (url.pathname.endsWith('/facts')) {
      const offset = Number(url.searchParams.get('offset') || 0);
      pages.push(offset);
      data = {snapshot_id:'s1',scope_hash:'b'.repeat(64),offset,has_more:offset===0,items:[{fact_id:`f${offset}`,label:'Giá trị',
        raw_value:'9007199254740993.01',value:'9007199254740993.01',unit:'VND',
        locator:{doc_id:'17',version:'3',sheet:'Sổ',cell:offset?'B52':'B2',row_key:`row-${offset+2}`}}]};
    }
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/chat');
  await page.locator('textarea').fill('So sánh hai kỳ');
  await page.getByRole('button',{name:'Gửi',exact:true}).click();
  await expect(page.locator('.citation-card')).toHaveCount(2);
  await expect(page.getByText('ledger.xlsx · Sheet Sổ · Ô B2', {exact:false})).toBeVisible();
  await expect(page.getByText('ledger.xlsx · Sheet Sổ · Ô B3', {exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Xem số liệu nguồn',exact:true}).first().click();
  await expect(page.getByRole('table')).toContainText('9007199254740993.01');
  await page.getByRole('button',{name:'Trang sau',exact:true}).click();
  await expect(page.getByRole('table')).toContainText('B52');
  expect(pages).toEqual([0,50]);
  await page.getByRole('table').scrollIntoViewIfNeeded();
  await page.screenshot({path:'../reports/rag-14-departments-20261005/structured-source-chat-ui-20261006.png',fullPage:true});
});

test('reopened history rejects a changed reviewed scope in the same snapshot', async ({page}) => {
  const citation = {doc_id:17,page_no:null,file_name:'ledger.xlsx',version_no:3,has_vision:false,
    original_url:'/api/files/documents/17/original',source_id:'D17L0123456789ABCDEF',
    citation_schema:'source-locator-v2',scope_id:'a',snapshot_id:'s1',scope_hash:'b'.repeat(64),
    locator:{doc_id:'17',version:'3',sheet:'Sổ',cell:'B2',row_key:'row-2'}};
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let data:unknown = {};
    if (url.pathname === '/api/auth/me') data = {ok:true,user:{user_id:7,username:'offline-reader',roles:['viewer'],
      allowed_departments:['Accountant'],allowed_sites:['HQ'],max_security_level:'internal',csrf_token:'offline-csrf'}};
    else if (url.pathname === '/api/chat/sessions') data = {sessions:[{session_id:'old-session',cau_hoi:'So sánh kỳ đã lưu'}]};
    else if (url.pathname === '/api/chat/history' && route.request().method()==='POST') {
      data = {messages:[{role:'assistant',chat_id:11,content:'Tổng A: 4,00 VND',citations:[citation]}]};
    } else if (url.pathname.endsWith('/facts') && route.request().method()==='GET') {
      data = {snapshot_id:'s1',scope_hash:'c'.repeat(64),offset:0,has_more:false,
        items:[{fact_id:'changed',label:'Giá trị',raw_value:'4',value:'6',unit:'VND',locator:citation.locator}]};
    } else if (route.request().method() !== 'GET') return route.abort();
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/chat');
  await page.getByRole('button',{name:'Lịch sử',exact:true}).click();
  await page.getByRole('button',{name:'So sánh kỳ đã lưu',exact:true}).click();
  await expect(page.locator('.citation-card')).toHaveCount(1);
  await page.getByRole('button',{name:'Xem số liệu nguồn',exact:true}).click();
  await expect(page.getByText('Nguồn đã thay đổi', {exact:false})).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.getByText('Tổng A: 4,00 VND', {exact:true})).toHaveCount(0);
  await expect(page.locator('.citation-card')).toHaveCount(0);
  await page.screenshot({path:'../reports/rag-14-departments-20261005/structured-history-source-pin-20261006.png',fullPage:true});
});
