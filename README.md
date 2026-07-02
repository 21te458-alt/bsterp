# 乐天店铺管理系统

基于 Flask 的乐天店铺综合管理系统，支持商品管理、库存管理、订单管理、利益计算和每日数据总结。

## 功能特点

- 📋 商品管理 - 8种分类自动识别
- 📊 在庫管理 - 实时库存查询与出库记录
- 🛒 贩卖信息 - 订单数据可视化
- 💰 利益管理 - 运费/利益自动计算
- 📈 每日总结 - 销售数据分析看板

## 部署到 Render

1. Fork 本仓库
2. 在 Render 创建 Web Service
3. 设置环境变量：
   - `SERVICE_SECRET`: 你的乐天服务密钥
   - `LICENSE_KEY`: 你的乐天许可证密钥

## 本地开发

```bash
pip install -r requirements.txt
python app.py
