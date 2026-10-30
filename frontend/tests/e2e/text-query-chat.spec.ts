import { expect,test } from '@playwright/test';

test('text evaluation sends one flag and shows cited evidence per intent',async({page,context})=>{
  let body:Record<string,unknown>|undefined;
  const source={doc_id:17,page_no:2,source_id:'D17P2',file_name:'fixture-policy.pdf',has_vision:false,
    original_url:'/api/files/documents/17/original'};
  // Every API endpoint is a synthetic fixture. This is browser transport evidence.
  await context.route('**/api/**',async route=>{
    const url=new URL(route.request().url());
    if (url.pathname==='/api/chat/message') {
      body=route.request().postDataJSON();
      return route.fulfill({status:200,contentType:'text/event-stream',body:
        'event: delta\ndata: '+JSON.stringify({text:'Câu trả lời chung có căn cứ [SRC:D17P2].'})+'\n\n'+
        'event: done\ndata: '+JSON.stringify({citations:[source],advanced_rag:{text_query:{schema:'query-text-v2',accepted:false,
          offline_only:true,branches:[{branch_id:'branch-1',subquery:'Quy trình nghỉ phép',outcome:'full_answer',cited_sources:[source]},
            {branch_id:'branch-2',subquery:'Đào tạo',outcome:'insufficient_evidence',cited_sources:[]}]}}})+'\n\n'});
    }
    let data:unknown={};
    if (url.pathname==='/api/auth/me') data={ok:true,user:{user_id:7,username:'fixture-reviewer',roles:['reviewer'],
      allowed_departments:['HR'],allowed_sites:['factory'],max_security_level:'internal',csrf_token:'fixture-csrf'}};
    else if (url.pathname.endsWith('evaluation-capabilities')) data={schema:'advanced-rag-v2-evaluation',accepted:false,
      offline_only:true,max_branches:3,text_query:true,actions:['contains','where_used','bom_rollup','scope_comparison']};
    else if (url.pathname==='/api/documents') data={documents:[]};
    else if (url.pathname==='/api/chat/sessions') data={sessions:[]};
    else if (route.request().method()!=='GET') return route.abort();
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/chat');
  await page.getByRole('checkbox',{name:'Chọn tác vụ có căn cứ (đánh giá)',exact:true}).check();
  await page.getByLabel('Chế độ đánh giá',{exact:true}).selectOption('text');
  await page.locator('textarea').fill('Quy trình nghỉ phép; đào tạo');
  await page.getByRole('button',{name:'Gửi',exact:true}).click();
  const evidence=page.getByRole('region',{name:'Chứng cứ từng ý'});
  await expect(evidence).toBeVisible();
  expect(body!.text_query).toBe(true);expect(body).not.toHaveProperty('query_plan');
  await expect(evidence).toContainText('Đủ căn cứ truy xuất');
  await expect(evidence).toContainText('Chưa đủ căn cứ');
  await expect(evidence.getByRole('link',{name:'fixture-policy.pdf',exact:true})).toHaveAttribute('href',source.original_url);
  await page.screenshot({path:'../reports/rag-14-departments-20261005/text-query-ui-graph-scope-20261007.png',fullPage:true});
  await page.getByRole('button',{name:'Cuộc trò chuyện mới',exact:true}).click();
  await expect(evidence).toHaveCount(0);
  await expect(page.getByRole('checkbox',{name:'Chọn tác vụ có căn cứ (đánh giá)',exact:true})).not.toBeChecked();
});
