import { test, expect } from '@playwright/test';

test('same-number ball finishes have independent inventory and survive refresh', async({page})=>{
  await page.goto('/');
  await page.getByLabel('系列',{exact:true}).selectOption('151C');
  await page.getByLabel('搜索卡牌').fill('妙蛙种子');
  await page.getByRole('button',{name:'查看 妙蛙种子 001',exact:true}).click();
  const panel=page.getByRole('region',{name:'卡面版本与收藏'});
  await expect(panel.getByText('大师球版',{exact:true})).toBeVisible();
  const master=panel.locator('[data-variant="chs-variant:11688"]');
  const ball=panel.locator('[data-variant="chs-variant:11549"]');
  await master.getByRole('spinbutton').fill('2');
  await master.getByRole('button',{name:'保存卡面数量',exact:true}).click();
  await expect(panel.getByText('此品相共 2 张 · 未分配卡面 0 张')).toBeVisible();
  await ball.getByRole('spinbutton').fill('3');
  await ball.getByRole('button',{name:'保存卡面数量',exact:true}).click();
  await expect(panel.getByText('此品相共 5 张 · 未分配卡面 0 张')).toBeVisible();
  await page.getByLabel('关闭窗口').click();
  await page.getByRole('button',{name:/我的收藏/}).click();
  const row=page.locator('.table-row').filter({has:page.getByLabel('选择 CN:151C:001',{exact:true})});
  await expect(row.locator('strong')).toHaveText('5');
  await row.getByRole('button',{name:'管理'}).click();
  await expect(master.getByRole('spinbutton')).toHaveValue('2');
  await expect(ball.getByRole('spinbutton')).toHaveValue('3');
  await master.getByRole('spinbutton').fill('1');
  await master.getByRole('button',{name:'保存卡面数量',exact:true}).click();
  await expect(panel.getByText('此品相共 4 张 · 未分配卡面 0 张')).toBeVisible();
  await page.screenshot({path:'../../var/card-variants.png',fullPage:true});
});
