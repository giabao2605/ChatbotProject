import {expect,test} from '@playwright/test';

test('history loading drops old evidence, keeps the latest session and handles 503 safely',async({page,context})=>{
  let finish:()=>void=()=>{};
  let started:()=>void=()=>{};
  const pending=new Promise<void>(resolve=>{started=resolve;});
  const held=new Promise<void>(resolve=>{finish=resolve;});
  const source={doc_id:17,page_no:2,source_id:'D17P2',file_name:'old-source.pdf',has_vision:false,
    original_url:'/api/files/documents/17/original'};
  await context.route('**/api/**',async route=>{
    const url=new URL(route.request().url());let data:unknown={};
    if (url.pathname==='/api/auth/me') data={ok:true,user:{user_id:7,username:'fixture',roles:['viewer'],
      allowed_departments:['Technical'],allowed_sites:['factory'],max_security_level:'internal',csrf_token:'fixture'}};
    else if (url.pathname==='/api/chat/sessions') data={sessions:[{session_id:'a',cau_hoi:'A'},
      {session_id:'slow',cau_hoi:'Slow'},{session_id:'b',cau_hoi:'B'},{session_id:'error',cau_hoi:'Error'}]};
    else if (url.pathname==='/api/chat/history') {
      const id=route.request().postDataJSON().session_id;
      if (id==='error') return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'private storage error'})});
      if (id==='slow') {started();await held;}
      data={messages:[{role:'assistant',content:id==='a'?'Old answer':id==='b'?'Current B':'Late answer',
        ...(id==='a'?{citations:[source],advanced_rag:{text_query:{schema:'query-text-v2',accepted:false,offline_only:true,
          branches:[{branch_id:'b1',subquery:'Old evidence',outcome:'full_answer',cited_sources:[source]}]}}}:{})}]};
    } else if (route.request().method()!=='GET') return route.abort();
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/chat');
  await page.getByRole('button',{name:'Lịch sử',exact:true}).click();
  await page.getByRole('button',{name:'A',exact:true}).click();
  await expect(page.getByRole('region',{name:'Chứng cứ từng ý'})).toBeVisible();
  await page.getByRole('button',{name:'Lịch sử',exact:true}).click();
  await page.getByRole('button',{name:'Slow',exact:true}).click();await pending;
  await expect(page.getByRole('status')).toContainText('Đang tải lịch sử');
  await expect(page.getByRole('region',{name:'Chứng cứ từng ý'})).toHaveCount(0);
  await page.locator('textarea').fill('Question');await expect(page.getByRole('button',{name:'Gửi',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'B',exact:true}).click();
  await expect(page.locator('.message-scroll')).toContainText('Current B');
  finish();await expect(page.getByRole('status')).toHaveCount(0);
  await expect(page.locator('.message-scroll')).not.toContainText('Late answer');
  await page.getByRole('button',{name:'Lịch sử',exact:true}).click();
  await page.getByRole('button',{name:'Error',exact:true}).click();
  await expect(page.getByText('Không tải được lịch sử. Hãy thử lại.',{exact:true})).toBeVisible();
  await expect(page.getByRole('alert')).toBeInViewport();
  await expect(page.locator('.message-scroll')).not.toContainText('Current B');
  await expect(page.locator('body')).not.toContainText('private storage error');
  await page.screenshot({path:'../reports/rag-14-departments-20261005/history-loading-error-final-20261007.png',fullPage:true,animations:'disabled'});
});
