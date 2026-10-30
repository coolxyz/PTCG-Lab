import {test,expect} from '@playwright/test';

test('only online and clone sources are offered, with a checked local path and no ZIP/upload controls',async({page})=>{
  let settings={source:'github-raw',baseUrl:'',capacityMiB:1024,localGitPath:''};
  let checked='',saved:any;
  const obsolete:string[]=[];
  await page.route('**/api/sync/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.includes('/resources'))obsolete.push(path);
    if(path.endsWith('/status'))return route.fulfill({json:{jobs:[],repository:'https://github.com/example/Cards'}});
    if(path.endsWith('/local-check')){checked=route.request().postDataJSON().path;return route.fulfill({json:{valid:true,message:'目录可读取，6 张示例卡图通过校验'}});}
    if(route.request().method()==='PUT'){saved=route.request().postDataJSON();settings=saved;}
    return route.fulfill({json:{...settings,indexedCards:12931,cachedImages:0,cachedBytes:0,retryAfterSeconds:0}});
  });
  await page.goto('/local-settings-fixture.html');
  await page.getByText('图片来源与缓存设置',{exact:true}).click();
  await expect(page.getByLabel('卡图显示方式').locator('option')).toHaveCount(2);
  await expect(page.locator('input[type=file]')).toHaveCount(0);
  await page.getByLabel('卡图显示方式').selectOption('local-git');
  await page.getByLabel('本地 Git 仓库目录').fill('/example/clone');
  await page.getByRole('button',{name:'检查本地目录'}).click();
  await expect(page.getByText('目录可读取，6 张示例卡图通过校验',{exact:true})).toBeVisible();
  expect(checked).toBe('/example/clone');
  await page.getByRole('button',{name:'保存设置并刷新卡图'}).click();
  await expect(page.getByText('设置已生效',{exact:true})).toBeVisible();
  expect(saved.source).toBe('local-git');expect(saved.localGitPath).toBe('/example/clone');expect(obsolete).toEqual([]);
});

test('a failed directory check keeps the draft editable and does not save it',async({page})=>{
  let writes=0;
  await page.route('**/api/sync/**',async route=>{
    if(route.request().method()==='PUT')writes++;
    if(route.request().url().endsWith('/local-check'))return route.fulfill({status:422,json:{message:'请选择包含 .git 和 img 的克隆仓库根目录'}});
    return route.fulfill({json:route.request().url().endsWith('/status')?{jobs:[]}:{source:'github-raw',baseUrl:'',capacityMiB:1024,localGitPath:'',cachedImages:0,cachedBytes:0}});
  });
  await page.goto('/local-settings-fixture.html');
  await page.getByText('图片来源与缓存设置',{exact:true}).click();
  await page.getByLabel('卡图显示方式').selectOption('local-git');
  await page.getByLabel('本地 Git 仓库目录').fill('/missing');
  await page.getByRole('button',{name:'检查本地目录'}).click();
  await expect(page.getByRole('alert')).toContainText('请选择包含 .git 和 img');
  await expect(page.getByLabel('本地 Git 仓库目录')).toHaveValue('/missing');
  expect(writes).toBe(0);
});
