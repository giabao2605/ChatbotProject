import { expect, test } from '@playwright/test';

test('three selected business actions reach chat and preserve each source and Math step',async({page,context})=>{
  const graph={root_key:'document:1',product_scope:'fixture-product',site:'factory',direction:'contains'};
  const citation={doc_id:17,page_no:null,file_name:'fixture-ledger.xlsx',source_id:'D17L1234567890ABCDEF',
    citation_schema:'source-locator-v2',scope_id:'a',snapshot_id:'fixture-snapshot',scope_hash:'b'.repeat(64),
    has_vision:false,original_url:'/api/files/documents/17/original?scope_id=a',
    locator:{doc_id:'17',version:'3',sheet:'Sổ',cell:'B2',row_key:'r2'}};
  const factsPages:number[]=[];
  let sent:Record<string,unknown>|undefined;
  // All API traffic is a synthetic fixture; this is browser transport evidence.
  await context.route('**/api/**',async route=>{
    const request=route.request(), url=new URL(request.url());
    if (url.pathname==='/api/chat/message') {
      sent=request.postDataJSON();
      return route.fulfill({status:200,contentType:'text/event-stream',body:
        'event: delta\ndata: '+JSON.stringify({text:'Kết quả theo ba ý đã chọn.'})+'\n\n'+
        'event: done\ndata: '+JSON.stringify({advanced_rag:{query:{schema:'query-plan-v2',status:'partial',accepted:false,
          offline_only:true,branches:[{branch_id:'b1',question:'Cụm chứa gì?',status:'partial',data_complete:true,
            graph:{source_status:'complete'},sources:[]},
            {branch_id:'b2',question:'So sánh hai phạm vi',status:'complete',sources:[citation],math:{steps:[
              {step_id:'sum_a',formula:'SUM(A)',display_value:'9007199254740993.01',unit:'VND'},
              {step_id:'sum_b',formula:'SUM(B)',display_value:'9007199254740996.01',unit:'VND'},
              {step_id:'delta',formula:'B-A',display_value:'3.00',unit:'VND'},
              {step_id:'percent_change',formula:'(B-A)/A*100',display_value:'0.00',status:'valid',unit:'%'}]}},
            {branch_id:'b3',question:'Tổng hợp BOM',status:'blocked',sources:[]}]}}})+'\n\n'});
    }
    let data:unknown={};
    if (url.pathname==='/api/auth/me') data={ok:true,user:{user_id:7,username:'fixture-reviewer',roles:['reviewer'],
      allowed_departments:['Technical','Accountant'],allowed_sites:['factory'],max_security_level:'internal',csrf_token:'fixture-csrf'}};
    else if (url.pathname.endsWith('evaluation-capabilities')) data={schema:'advanced-rag-v2-evaluation',accepted:false,
      offline_only:true,max_branches:3,actions:['contains','where_used','bom_rollup','scope_comparison']};
    else if (url.pathname==='/api/documents') data={documents:[{DocID:1,OriginalFileName:'fixture-assembly.pdf'},
      {DocID:17,OriginalFileName:'fixture-ledger.xlsx'}, {DocID:99,OriginalFileName:'fixture-policy.csv'}]};
    else if (url.pathname.endsWith('/graph-scopes')) data={items:[{product_scope:'fixture-product',site:'factory'}],offset:0,has_more:false};
    else if (url.pathname.endsWith('/approved-scopes')) data={items:[{scope_id:'a',definition:'Kỳ A',period:'Tháng 6',unit:'VND'},
      {scope_id:'b',definition:'Kỳ B',period:'Tháng 7',unit:'VND'},
      {scope_id:'policy',definition:'Số lượng',period:'',unit:'piece'}],offset:0,has_more:false};
    else if (url.pathname==='/api/rag-evidence/approved-scopes/a/facts') {
      const offset=Number(url.searchParams.get('offset'));factsPages.push(offset);
      data={items:[{fact_id:`row-${offset}`,label:'Giá trị',raw_value:'9007199254740993.01',value:'9007199254740993.01',unit:'VND',
        locator:{doc_id:'17',version:'3',sheet:'Sổ',cell:offset?'B52':'B2',row_key:`r${offset+2}`}}],
        snapshot_id:'fixture-snapshot',scope_hash:'b'.repeat(64),offset,has_more:offset===0};
    } else if (url.pathname==='/api/chat/sessions') data={sessions:[]};
    else if (request.method()!=='GET') return route.abort();
    return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('/chat');
  await page.getByRole('checkbox',{name:'Chọn tác vụ có căn cứ (đánh giá)',exact:true}).check();
  await page.getByLabel('Bản vẽ ý 1',{exact:true}).selectOption('1');
  await page.getByLabel('Phạm vi sản phẩm ý 1',{exact:true}).selectOption('fixture-product');
  await page.getByRole('button',{name:'Thêm ý',exact:true}).click();
  await page.getByLabel('Tác vụ ý 2',{exact:true}).selectOption('scope_comparison');
  for (const [label,scope] of [['Phạm vi A ý 2','a'],['Phạm vi B ý 2','b']]) {
    await page.getByLabel(`Tài liệu ${label}`,{exact:true}).selectOption('17');
    await page.getByLabel(label,{exact:true}).selectOption(scope);
  }
  await page.getByRole('button',{name:'Thêm ý',exact:true}).click();
  await page.getByLabel('Tác vụ ý 3',{exact:true}).selectOption('bom_rollup');
  await page.getByLabel('Bản vẽ ý 3',{exact:true}).selectOption('1');
  await page.getByLabel('Phạm vi sản phẩm ý 3',{exact:true}).selectOption('fixture-product');
  await page.getByLabel('Số lượng ý 3',{exact:true}).fill('9007199254740993.01');
  await page.getByLabel('Tài liệu Quy tắc số lượng ý 3',{exact:true}).selectOption('99');
  await page.getByLabel('Quy tắc số lượng ý 3',{exact:true}).selectOption('policy');
  await expect(page.getByRole('button',{name:'Thêm ý',exact:true})).toBeDisabled();
  await page.locator('.advanced-actions').evaluate(node=>{node.scrollTop=0;});
  await page.screenshot({path:'../reports/rag-14-departments-20261005/graph-scope-ui-selector-20261007.png',fullPage:true});
  await page.locator('textarea').fill('Tra cây cụm; so sánh hai kỳ; tính BOM');
  await page.getByRole('button',{name:'Gửi',exact:true}).click();
  const results=page.getByRole('region',{name:'Kết quả từng ý'});
  await expect(results).toBeVisible();
  expect(sent!.query_plan).toEqual({branches:[
    {branch_id:'b1',question:'Cụm chứa gì?',depends_on:[],graph_query:graph},
    {branch_id:'b2',question:'So sánh hai phạm vi',depends_on:[],scope_comparison:{scope_a:'a',scope_b:'b'}},
    {branch_id:'b3',question:'Tổng hợp BOM',depends_on:[],bom_rollup:{...graph,
      order_quantity:'9007199254740993.01',rounding_scope:'policy'}}]});
  await expect(results).toContainText('Cây nguồn: Đầy đủ');
  await expect(results).toContainText('Chưa đủ dữ liệu');
  await expect(results.getByRole('table')).toContainText('9007199254740993.01');
  await expect(results.getByRole('table')).toContainText('0.00');
  await results.locator('summary').click();
  await results.getByRole('button',{name:'Xem số liệu nguồn',exact:true}).click();
  await expect(results.locator('.source-facts')).toContainText('B2');
  await results.getByRole('button',{name:'Trang sau',exact:true}).click();
  await expect(results.locator('.source-facts')).toContainText('B52');
  expect(factsPages).toEqual([0,50]);
  await page.getByRole('checkbox',{name:'Chọn tác vụ có căn cứ (đánh giá)',exact:true}).uncheck();
  await page.screenshot({path:'../reports/rag-14-departments-20261005/graph-scope-ui-source-20261007.png',fullPage:true});
  await page.locator('.message-scroll').evaluate(node=>{node.scrollTop=0;});
  await page.screenshot({path:'../reports/rag-14-departments-20261005/graph-scope-ui-results-20261007.png',fullPage:true});
  await page.getByRole('button',{name:'Cuộc trò chuyện mới',exact:true}).click();
  await expect(results).toHaveCount(0);
});

for (const mode of ['viewer','production']) {
  test(`ordinary chat has no v2 selector for ${mode}`,async({page})=>{
    await page.route('**/api/**',async route=>{
      const url=new URL(route.request().url());
      if (url.pathname.endsWith('evaluation-capabilities')) return route.fulfill({status:409,contentType:'application/json',
        body:JSON.stringify({detail:'V2 contract requires evaluation context'})});
      const data=url.pathname==='/api/auth/me'?{ok:true,user:{user_id:7,username:'fixture',roles:[mode==='viewer'?'viewer':'reviewer'],
        allowed_departments:['Technical'],allowed_sites:['factory'],max_security_level:'internal',csrf_token:'fixture-csrf'}}:{sessions:[]};
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto('/chat');
    await expect(page.getByRole('heading',{name:'Trợ lý tài liệu nội bộ'})).toBeVisible();
    await expect(page.getByRole('checkbox',{name:'Chọn tác vụ có căn cứ (đánh giá)'})).toHaveCount(0);
  });
}
