import {expect,test} from '@playwright/test';

for (const kind of ['query','text_query'] as const) {
  test(`history restores ${kind} branch states and drops them after sources are revoked`,async({page,context})=>{
    let revoked=false;
    const source={doc_id:17,version_no:3,page_no:2,source_id:'D17P2',file_name:'fixture-source.pdf',
      has_vision:false,original_url:'/api/files/documents/17/original'};
    const result=kind==='query'?{query:{schema:'query-plan-v2',status:'partial',accepted:false,offline_only:true,branches:[
      {branch_id:'b1',question:'So sánh',status:'complete',sources:[source],math:{steps:[
        {step_id:'delta',formula:'B-A',display_value:'9007199254740993.01',unit:'VND'}]}},
      {branch_id:'b2',question:'Ý thiếu nguồn',status:'blocked',sources:[]}]}}:
      {text_query:{schema:'query-text-v2',accepted:false,offline_only:true,branches:[
        {branch_id:'b1',subquery:'Quy trình',outcome:'full_answer',cited_sources:[source]},
        {branch_id:'b2',subquery:'Ý thiếu nguồn',outcome:'insufficient_evidence',cited_sources:[]}]}};
    // All API responses are fixtures. SQL authorization is tested separately.
    await context.route('**/api/**',async route=>{
      const url=new URL(route.request().url());
      let data:unknown={};
      if (url.pathname==='/api/auth/me') data={ok:true,user:{user_id:7,username:'fixture-reader',roles:['viewer'],
        allowed_departments:['Technical'],allowed_sites:['factory'],max_security_level:'internal',csrf_token:'fixture-csrf'}};
      else if (url.pathname==='/api/chat/sessions') data={sessions:[{session_id:'saved',cau_hoi:'Lịch sử nhiều ý'}]};
      else if (url.pathname==='/api/chat/history') data={messages:[{role:'user',content:'Question'},
        revoked?{role:'assistant',content:'Nội dung đã được ẩn vì không còn xác minh được nguồn.',sources_redacted:true,citations:[]}:
        {role:'assistant',content:'Kết quả [SRC:D17P2]',citations:[source],advanced_rag:result}]};
      else if (route.request().method()!=='GET') return route.abort();
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto('/chat');
    await page.getByRole('button',{name:'Lịch sử',exact:true}).click();
    await page.getByRole('button',{name:'Lịch sử nhiều ý',exact:true}).click();
    const region=page.getByRole('region',{name:kind==='query'?'Kết quả từng ý':'Chứng cứ từng ý'});
    await expect(region).toBeVisible();
    if (kind==='query') {
      await expect(region.getByRole('table')).toContainText('9007199254740993.01');
      await region.locator('summary').click();
    }
    await expect(region.getByRole('link',{name:'fixture-source.pdf',exact:true})).toHaveAttribute('href',source.original_url);
    await page.screenshot({path:`../reports/rag-14-departments-20261005/branch-history-${kind}-20261007.png`,fullPage:true});
    revoked=true;
    await page.getByRole('button',{name:'Lịch sử',exact:true}).click();
    await page.getByRole('button',{name:'Lịch sử nhiều ý',exact:true}).click();
    await expect(region).toHaveCount(0);
    await expect(page.locator('.message-scroll')).toContainText('Nội dung đã được ẩn');
    await expect(page.getByRole('link',{name:'fixture-source.pdf',exact:true})).toHaveCount(0);
  });
}
