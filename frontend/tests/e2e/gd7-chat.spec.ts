import { expect,test } from '@playwright/test';

const question='Rà các quy định liên quan';
const original='/api/files/documents/17/original?citation_version=2&source_hash=fixture-hash&snapshot_id=fixture-snapshot';
const citation={doc_id:17,page_no:2,source_id:'D17P2',evidence_identity:'doc17-v2-p2-a',version_no:2,
  file_name:'fixture-policy.pdf',has_vision:false,original_url:original};
const progress={search_mode:'comprehensive',scan_id:'scan-fixture',scan_status:'pending',total_sources:2,
  checked_sources:1,pending_sources:1,error_sources:0,snapshot_complete:false,turn_complete:true,result_revision:1,continuation_cursor:'cursor-1'};
const event=(name:string,data:unknown)=>`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

test('comprehensive progress, stop, replay, next revision and pinned source with keyboard',async({page,context})=>{
  let calls=0;const requests:Record<string,unknown>[]=[];let blocked:()=>void=()=>{};
  const hold=new Promise<void>(resolve=>{blocked=resolve;});
  await context.route('**/api/**',async route=>{
    const url=new URL(route.request().url());
    if (url.pathname==='/api/chat/message') {
      requests.push(route.request().postDataJSON());calls++;
      if (calls===2) {await hold;return route.abort().catch(()=>{});}
      const next=calls>=4;
      const p=next ? {...progress,checked_sources:2,pending_sources:0,snapshot_complete:true,scan_status:'complete',
        result_revision:calls===4 ? 2 : 3,continuation_cursor:calls===4 ? 'cursor-2' : 'cursor-3',results_pending:calls===4} : progress;
      return route.fulfill({contentType:'text/event-stream',body:event('thinking',{search_progress:{...p,turn_complete:false}})+
        event('delta',{text:next ? 'Nội dung nguồn B.' : 'Nội dung nguồn A.'})+
        event('done',{search_progress:p,citations:next ? [{...citation,evidence_identity:'doc17-v2-p2-b'}] : [citation],verification_status:'verified'})});
    }
    if(url.pathname==='/api/files/documents/17/original') {
      expect(url.pathname+url.search).toBe(original);
      return route.fulfill({contentType:'application/pdf',body:'%PDF-1.4 fixture'});
    }
    const data=url.pathname==='/api/auth/me' ? {ok:true,user:{user_id:7,username:'fixture',roles:['viewer'],
      allowed_departments:['HR'],allowed_sites:['factory'],max_security_level:'internal',csrf_token:'fixture'}} :
      url.pathname==='/api/chat/sessions' ? {sessions:[]} : {};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/chat');
  await expect(page.getByLabel('Cách tra cứu')).toHaveValue('quick');
  await page.getByLabel('Cách tra cứu').selectOption('comprehensive');
  await page.getByLabel('Câu hỏi',{exact:true}).fill(question);
  await page.getByRole('button',{name:'Gửi',exact:true}).click();
  await expect(page.getByRole('region',{name:'Tiến độ rà nguồn'})).toContainText('đã kiểm 1/2');
  await expect(page.getByText('Lần rà chưa hoàn tất.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Tiếp tục rà',exact:true}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button',{name:'Dừng trả lời'})).toBeVisible();
  await page.getByRole('button',{name:'Dừng trả lời'}).focus();await page.keyboard.press('Enter');
  await expect(page.getByLabel('Câu hỏi',{exact:true})).toBeFocused();
  blocked();
  await expect(page.getByRole('button',{name:'Tiếp tục rà',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Tiếp tục rà',exact:true}).click();
  await expect(page.locator('.citation-card')).toHaveCount(1);
  await expect(page.locator('.message-content')).toHaveText('Nội dung nguồn A.');
  await page.getByRole('button',{name:'Tiếp tục rà',exact:true}).click();
  await expect(page.locator('.citation-card')).toHaveCount(2);
  await expect(page.getByText('Đã rà xong phạm vi nguồn; còn kết quả đã lưu chưa nhận.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Xem tiếp kết quả đã lưu',exact:true}).click();
  await expect(page.getByText('Đã rà xong toàn bộ phạm vi nguồn được phép của lần rà này.',{exact:true})).toBeVisible();
  expect(requests).toHaveLength(5);
  for (const request of requests.slice(1,4)) {
    expect(request.question).toBe(question);expect(request.search_mode).toBe('comprehensive');
    expect(request.continuation_cursor).toBe('cursor-1');
  }
  expect(requests[4].continuation_cursor).toBe('cursor-2');
  const link=page.getByRole('link',{name:'Tải bản gốc fixture-policy.pdf, phiên bản 2'}).first();
  await expect(link).toHaveAttribute('href',original);
  await link.focus();
  const downloaded=page.waitForEvent('download');await page.keyboard.press('Enter');await downloaded;
  await page.screenshot({path:'../.local/gd7-ui-fixture.png',fullPage:true});
});

test('backend promotion and stale response hide old results and offer restart',async({page,context})=>{
  let calls=0;
  await context.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/chat/message') {
      calls++;
      return route.fulfill({contentType:'text/event-stream',body:calls===1 ?
        event('thinking',{search_progress:progress})+event('delta',{text:'Protected result'})+
          event('done',{search_progress:progress,citations:[citation]}) :
        event('done',{search_progress:{search_mode:'comprehensive',scan_status:'stale',snapshot_complete:false,turn_complete:true}})});
    }
    const data=path==='/api/auth/me' ? {ok:true,user:{user_id:7,username:'fixture',roles:['viewer'],
      allowed_departments:['HR'],allowed_sites:['factory'],max_security_level:'internal',csrf_token:'fixture'}} :
      path==='/api/chat/sessions' ? {sessions:[]} : {};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/chat');await page.getByLabel('Câu hỏi',{exact:true}).fill(question);
  await page.getByRole('button',{name:'Gửi',exact:true}).click();
  await expect(page.getByRole('region',{name:'Tiến độ rà nguồn'})).toContainText('Rà toàn bộ');
  await page.getByRole('button',{name:'Tiếp tục rà',exact:true}).click();
  await expect(page.getByText('Nguồn đã thay đổi. Hãy bắt đầu lại lần rà.',{exact:true})).toBeVisible();
  await expect(page.locator('.citation-card')).toHaveCount(0);
  await expect(page.getByText('Protected result',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Tiếp tục rà',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Bắt đầu lại lần rà'}).focus();await page.keyboard.press('Enter');
  await expect(page.getByLabel('Câu hỏi',{exact:true})).toHaveValue(question);
  await expect(page.getByLabel('Câu hỏi',{exact:true})).toBeFocused();
});
