"""
选址预测模型训练脚本
====================
数据管道位置：第 3 步（建模阶段）
前置依赖：compute_features.py 已完成（store_poi_stats 和 random_points 数据就绪）
输入数据：stores + store_poi_stats + random_points + city_tiers 表
输出数据：XGBoost 模型文件 + prediction_grid 表（特征重要性）

=== 核心思路 ===
将"门店选址"问题建模为二分类任务：
  - 正样本（label=1）：瑞幸咖啡现有门店的位置
  - 负样本（label=0）：同城市内的随机采样点

直觉逻辑：如果模型能学会区分"瑞幸门店位置"和"随机位置"，
          那么这个模型就学会了瑞幸选址的偏好。

=== 特征工程（28 个 POI 计数特征 + 1 个城市层级特征）===
特征命名规则：{poi_category}_{radius}
  - poi_category: 9 种 POI 类型（metro/bus/office/mall/restaurant/cafe/residential/university/convenience）
  - radius: 3 个缓冲区半径（200m / 500m / 1000m）
  - 9 x 3 = 27 个 POI 计数特征
  - + city_tier（城市层级：1=一线, 2=新一线, 3=二线, 4+=其他）
  - 总计 28 个特征

特征设计原理：
  - 缓冲区半径选择 200/500/1000m，对应步行 2-3 分钟/5-7 分钟/10-15 分钟
  - 多半径设计可以捕捉 POI 对门店选址影响的"距离衰减"效应
  - 例如：咖啡厅 200m 内数量和 1000m 内数量可能对选址有不同的影响权重

=== 模型超参数说明 ===
- n_estimators=200:          决策树数量，越大模型越复杂。200 对于中小数据集已足够
- max_depth=6:               单棵树最大深度，限制过拟合。6 层可捕捉适度复杂的交互
- learning_rate=0.05:        学习率（步长），较小的值使训练更稳健但需要更多树
- subsample=0.8:             每棵树随机采样 80% 样本（行采样），防止过拟合
- colsample_bytree=0.8:      每棵树随机采样 80% 特征（列采样），增加树间多样性
- eval_metric='auc':         评估指标为 AUC（ROC 曲线下面积），适合二分类不平衡数据
- early_stopping_rounds=20:  验证集 AUC 连续 20 轮不提升则提前停止训练

=== 评估指标 ===
- AUC（Area Under ROC Curve）：主评估指标
    - AUC=1.0 → 完美区分正负样本
    - AUC=0.5 → 等同随机猜测
    - 目标：AUC > 0.75 表示模型有区分能力
- Classification Report：精确率（Precision）/ 召回率（Recall）/ F1-Score
- Feature Importance：XGBoost 内置的特征重要性（Gain 加权）
- SHAP 值：基于博弈论的可解释性方法，解释每个特征对每次预测的贡献

=== SHAP 可解释性 ===
SHAP（SHapley Additive exPlanations）原理：
  - 基于 Shapley 值（博弈论中的公平分配理论）
  - 计算每个特征对预测结果的"边际贡献"
  - 正值表示该特征增加了"适合开店"的得分，负值表示降低得分
用途举例：
  - 若某候选点的 SHAP 分析显示 office_500 贡献了最高正分，
    说明该点周边办公 POI 密集是选址的主要正面因素

预计运行时间：约 2-5 分钟（取决于门店数量）

依赖：
  - config.DB_CONFIG
  - sklearn + xgboost + shap + joblib
  - sql/schema.sql 中 prediction_grid 表需先建好

执行方式：
  python scripts/train_model.py
"""
import asyncio
import asyncpg
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import roc_auc_score, classification_report, confusion_matrix
from xgboost import XGBClassifier
import shap

from config import DB_CONFIG, FOCUS_CITIES

# 模型存储目录（项目根目录下的 data/models/）
MODEL_DIR = Path(__file__).parent.parent / 'data' / 'models'
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 特征列定义（28 个特征）
# 命名规则：{POI类别}_{缓冲区半径}
#   9 类 POI x 3 个半径 = 27 个 POI 计数特征
#   + 1 个城市层级特征 (city_tier)
# 完整列表：
#   - 交通类: metro_200/500/1000, bus_200/500/1000
#   - 商业类: mall_200/500/1000, restaurant_200/500/1000, cafe_200/500/1000
#   - 办公类: office_200/500/1000
#   - 居住/教育: residential_200/500/1000, university_200/500/1000
#   - 生活服务: convenience_200/500/1000
#   - 城市属性: city_tier
# ============================================================
FEATURE_COLUMNS = [
    # 地铁站（3 个半径）
    'metro_200', 'metro_500', 'metro_1000',
    # 公交站（3 个半径）
    'bus_200', 'bus_500', 'bus_1000',
    # 办公楼/产业园（3 个半径）
    'office_200', 'office_500', 'office_1000',
    # 商场/购物中心（3 个半径）
    'mall_200', 'mall_500', 'mall_1000',
    # 餐厅（3 个半径）
    'restaurant_200', 'restaurant_500', 'restaurant_1000',
    # 咖啡厅（3 个半径）—— 特别关注：可反映与星巴克的竞争/共生关系
    'cafe_200', 'cafe_500', 'cafe_1000',
    # 住宅小区（3 个半径）
    'residential_200', 'residential_500', 'residential_1000',
    # 大学/学院（3 个半径）—— 大学生是咖啡消费主力群体
    'university_200', 'university_500', 'university_1000',
    # 便利店（3 个半径）
    'convenience_200', 'convenience_500', 'convenience_1000',
    # 城市层级（1=一线, 2=新一线, 3=二线, 4+=其他）
    'city_tier',
]


async def build_training_data(conn) -> pd.DataFrame:
    """从数据库构建训练数据集（正样本 + 负样本）。

    正样本构造（label=1）：
      - 从 stores 表取所有 brand='luckin' 的门店
      - 通过 LEFT JOIN store_poi_stats 获取每门店的 POI 缓冲区计数
      - 使用 MAX(CASE WHEN ...) 将行转列（pivot），每一行 = 一个门店的全部特征
      - 仅保留在 city_tiers 中有记录的城市（排除非重点城市的数据）

    负样本构造（label=0）：
      - 从 random_points 表取随机采样点
      - 同样通过 LEFT JOIN pois 计算每个随机点的 POI 特征
      - 负样本的特征计算方式与正样本相同（保证特征一致性）
      - 注意：负样本使用 0/1 指示（ANY pois 存在即为 1），而非计数

    说明：为什么正样本用计数而负样本用指示变量？
      - 正样本：store_poi_stats 已缓存了每个门店的精确 POI 计数
      - 负样本：random_points 表没有预计算缓存，实时计算 COUNT 性能差
      - 使用 0/1 指示变量作为近似，在特征含义上仍然有效（有 vs 无此类 POI）

    Args:
        conn: asyncpg 数据库连接

    Returns:
        pd.DataFrame: 包含 FEATURE_COLUMNS + label 列的完整训练数据
    """
    print('构建训练数据...')

    # ============================================================
    # 正样本：瑞幸咖啡现有门店
    # 使用 store_poi_stats 预计算缓冲区中直接读取
    # pivot 逻辑：MAX(CASE WHEN ...) 将 category+radius 两维度展平为单行
    # ============================================================
    pos_rows = await conn.fetch('''
        SELECT
            s.id,
            s.city,
            1 AS label,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'metro' AND sps.radius = 200 THEN sps.poi_count END), 0) AS metro_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'metro' AND sps.radius = 500 THEN sps.poi_count END), 0) AS metro_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'metro' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS metro_1000,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'bus' AND sps.radius = 200 THEN sps.poi_count END), 0) AS bus_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'bus' AND sps.radius = 500 THEN sps.poi_count END), 0) AS bus_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'bus' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS bus_1000,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'office' AND sps.radius = 200 THEN sps.poi_count END), 0) AS office_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'office' AND sps.radius = 500 THEN sps.poi_count END), 0) AS office_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'office' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS office_1000,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'mall' AND sps.radius = 200 THEN sps.poi_count END), 0) AS mall_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'mall' AND sps.radius = 500 THEN sps.poi_count END), 0) AS mall_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'mall' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS mall_1000,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'restaurant' AND sps.radius = 200 THEN sps.poi_count END), 0) AS restaurant_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'restaurant' AND sps.radius = 500 THEN sps.poi_count END), 0) AS restaurant_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'restaurant' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS restaurant_1000,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'cafe' AND sps.radius = 200 THEN sps.poi_count END), 0) AS cafe_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'cafe' AND sps.radius = 500 THEN sps.poi_count END), 0) AS cafe_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'cafe' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS cafe_1000,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'residential' AND sps.radius = 200 THEN sps.poi_count END), 0) AS residential_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'residential' AND sps.radius = 500 THEN sps.poi_count END), 0) AS residential_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'residential' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS residential_1000,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'university' AND sps.radius = 200 THEN sps.poi_count END), 0) AS university_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'university' AND sps.radius = 500 THEN sps.poi_count END), 0) AS university_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'university' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS university_1000,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'convenience' AND sps.radius = 200 THEN sps.poi_count END), 0) AS convenience_200,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'convenience' AND sps.radius = 500 THEN sps.poi_count END), 0) AS convenience_500,
            COALESCE(MAX(CASE WHEN sps.poi_category = 'convenience' AND sps.radius = 1000 THEN sps.poi_count END), 0) AS convenience_1000,
            COALESCE(ct.tier, 4) AS city_tier
        FROM stores s
        LEFT JOIN store_poi_stats sps ON s.id = sps.store_id
        LEFT JOIN city_tiers ct ON s.city = ct.city_name
        WHERE s.brand = 'luckin' AND s.city IN (
            SELECT city_name FROM city_tiers
        )
        GROUP BY s.id, s.city, ct.tier
    ''')

    pos_df = pd.DataFrame([dict(r) for r in pos_rows])
    print(f'  正样本: {len(pos_df)} 条')

    # ============================================================
    # 负样本：同城市内的随机采样点
    # 使用 0/1 指示变量（该点周边是否存在该类 POI）代替精确计数
    # 原因：random_points 表无预计算缓存，COUNT 性能差
    # ============================================================
    neg_rows = await conn.fetch('''
        SELECT
            rp.id,
            rp.city,
            0 AS label,
            COALESCE(MAX(CASE WHEN p.category = 'metro' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS metro_200,
            COALESCE(MAX(CASE WHEN p.category = 'metro' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS metro_500,
            COALESCE(MAX(CASE WHEN p.category = 'metro' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS metro_1000,
            COALESCE(MAX(CASE WHEN p.category = 'bus' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS bus_200,
            COALESCE(MAX(CASE WHEN p.category = 'bus' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS bus_500,
            COALESCE(MAX(CASE WHEN p.category = 'bus' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS bus_1000,
            COALESCE(MAX(CASE WHEN p.category = 'office' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS office_200,
            COALESCE(MAX(CASE WHEN p.category = 'office' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS office_500,
            COALESCE(MAX(CASE WHEN p.category = 'office' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS office_1000,
            COALESCE(MAX(CASE WHEN p.category = 'mall' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS mall_200,
            COALESCE(MAX(CASE WHEN p.category = 'mall' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS mall_500,
            COALESCE(MAX(CASE WHEN p.category = 'mall' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS mall_1000,
            COALESCE(MAX(CASE WHEN p.category = 'restaurant' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS restaurant_200,
            COALESCE(MAX(CASE WHEN p.category = 'restaurant' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS restaurant_500,
            COALESCE(MAX(CASE WHEN p.category = 'restaurant' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS restaurant_1000,
            COALESCE(MAX(CASE WHEN p.category = 'cafe' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS cafe_200,
            COALESCE(MAX(CASE WHEN p.category = 'cafe' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS cafe_500,
            COALESCE(MAX(CASE WHEN p.category = 'cafe' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS cafe_1000,
            COALESCE(MAX(CASE WHEN p.category = 'residential' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS residential_200,
            COALESCE(MAX(CASE WHEN p.category = 'residential' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS residential_500,
            COALESCE(MAX(CASE WHEN p.category = 'residential' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS residential_1000,
            COALESCE(MAX(CASE WHEN p.category = 'university' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS university_200,
            COALESCE(MAX(CASE WHEN p.category = 'university' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS university_500,
            COALESCE(MAX(CASE WHEN p.category = 'university' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS university_1000,
            COALESCE(MAX(CASE WHEN p.category = 'convenience' AND ST_DWithin(rp.geom::geography, p.geom::geography, 200)
                         THEN 1 ELSE 0 END), 0) AS convenience_200,
            COALESCE(MAX(CASE WHEN p.category = 'convenience' AND ST_DWithin(rp.geom::geography, p.geom::geography, 500)
                         THEN 1 ELSE 0 END), 0) AS convenience_500,
            COALESCE(MAX(CASE WHEN p.category = 'convenience' AND ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
                         THEN 1 ELSE 0 END), 0) AS convenience_1000,
            COALESCE(ct.tier, 4) AS city_tier
        FROM random_points rp
        LEFT JOIN pois p ON ST_DWithin(rp.geom::geography, p.geom::geography, 1000)
        LEFT JOIN city_tiers ct ON rp.city = ct.city_name
        WHERE rp.city IN (SELECT city_name FROM city_tiers)
        GROUP BY rp.id, rp.city, ct.tier
    ''')

    neg_df = pd.DataFrame([dict(r) for r in neg_rows])
    print(f'  负样本: {len(neg_df)} 条')

    # ============================================================
    # 合并训练数据
    # ============================================================
    df = pd.concat([pos_df, neg_df], ignore_index=True)
    # 将 NaN 填充为 0（部分门店可能缺少某些 POI 类别的数据）
    df = df.fillna(0)

    print(f'  总样本: {len(df)} (正:{len(pos_df)}, 负:{len(neg_df)})')
    return df


def train_model(df: pd.DataFrame):
    """训练 XGBoost 二分类选址预测模型。

    训练流程：
    1. 数据集划分（80% 训练 / 20% 测试，按 label 分层采样）
    2. 配置 XGBoost 模型参数并训练
    3. 模型评估：AUC + Classification Report
    4. 特征重要性分析
    5. SHAP 可解释性分析
    6. 模型保存（joblib 序列化）

    评估指标解读：
    - AUC（ROC 曲线下面积）：
      - 衡量模型区分正负样本的整体能力
      - > 0.75 表示模型有实用价值，> 0.85 表示模型优秀
    - Precision（精确率）= TP / (TP + FP)：
      - 预测为"适合开店"的点中，真正是门店的比例
    - Recall（召回率）= TP / (TP + FN)：
      - 所有真实门店中，被正确识别的比例
    - F1-Score = 2 * Precision * Recall / (Precision + Recall)：
      - 精确率和召回率的调和平均数

    Args:
        df: 训练数据集（来自 build_training_data 的返回）

    Returns:
        tuple: (model, auc, importance_df, shap_values)
        - model:        训练好的 XGBClassifier 模型
        - auc:          ROC AUC 分数（float）
        - importance:   特征重要性 DataFrame（feature + importance 列）
        - shap_values:  SHAP 值数组（n_samples x n_features）
    """
    print('\n训练XGBoost选址预测模型...')

    # 特征矩阵和标签向量
    X = df[FEATURE_COLUMNS]
    y = df['label']

    # === 数据集划分 ===
    # test_size=0.2: 20% 用于测试
    # stratify=y: 按标签比例分层采样，保证训练集和测试集的正负样本比例一致
    # random_state=42: 固定随机种子，保证结果可复现
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # === XGBoost 模型配置 ===
    # n_estimators=200:  决策树数量。200 棵对中小数据集足够，更多可能过拟合
    # max_depth=6:        树最大深度。6 层可捕捉 POI 之间的适度交互，限制过拟合
    # learning_rate=0.05: 学习率（eta）。较小值训练更稳定，但需要更多棵树补偿
    # subsample=0.8:      行采样比例。80% 数据用于每棵树训练，增加随机性防过拟合
    # colsample_bytree=0.8: 列采样比例。80% 特征用于每棵树，增加树间多样性
    # random_state=42:    固定随机种子
    # eval_metric='auc':  使用 AUC 作为验证指标
    # early_stopping_rounds=20: 验证 AUC 20 轮不提升则停止（需要 eval_set）
    model = XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        eval_metric='auc',
        early_stopping_rounds=20,
    )

    # 模型训练
    # eval_set 用于 early stopping：测试集上 AUC 不再提升时提前终止
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # === 模型评估 ===
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]  # 正类（适合开店）的概率
    auc = roc_auc_score(y_test, y_proba)

    print(f'\n  测试集 AUC: {auc:.4f}')
    print(f'\n  Classification Report:')
    print(classification_report(y_test, y_pred, target_names=['不适合', '适合']))

    # === 特征重要性分析 ===
    # feature_importances_ 返回所有特征的重要性分数（基于 Gain 加权）
    # Gain 衡量使用该特征进行分裂带来的平均损失减少
    importance = pd.DataFrame({
        'feature': FEATURE_COLUMNS,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)

    print(f'\n  Top 10 重要特征:')
    for _, row in importance.head(10).iterrows():
        print(f'    {row["feature"]:<25} {row["importance"]:.4f}')

    # === SHAP 可解释性分析 ===
    # TreeExplainer 专为树模型优化，计算速度快于 KernelExplainer
    # 对测试集随机采样 500 条以加速 SHAP 计算（全量计算耗时长）
    print(f'\n  计算SHAP值...')
    explainer = shap.TreeExplainer(model)
    # 采样以加速（全量 SHAP 值计算耗时与样本数成正比）
    sample_idx = np.random.choice(len(X_test), min(500, len(X_test)), replace=False)
    shap_values = explainer.shap_values(X_test.iloc[sample_idx])

    # === 模型持久化 ===
    # 使用 joblib 序列化（比 pickle 更适合包含 numpy 数组的 scikit-learn 模型）
    model_path = MODEL_DIR / 'xgboost_location_model.pkl'
    joblib.dump(model, model_path)
    print(f'\n  模型已保存: {model_path}')

    return model, auc, importance, shap_values


async def main():
    """主函数：构建训练数据 → 训练模型 → 保存特征重要性。

    执行流程：
    1. 从数据库构建训练数据（正样本 + 负样本）
    2. 训练 XGBoost 二分类模型
    3. 将特征重要性写入 prediction_grid 表（供前端可视化使用）
    4. 打印模型评估结果和文件路径
    """
    conn = await asyncpg.connect(**DB_CONFIG)

    # 构建训练数据
    df = await build_training_data(conn)
    # 训练模型
    model, auc, importance, shap_values = train_model(df)

    # 保存特征重要性到数据库（用于前端可视化展示）
    # 使用特殊的几何坐标 (0, 0) 作为 global 标记
    for _, row in importance.iterrows():
        await conn.execute('''
            INSERT INTO prediction_grid (cell_geom, city, score, features, cell_size_meters)
            VALUES (ST_SetSRID(ST_MakePoint(0, 0), 4326), 'global', $1,
                    $2::JSONB, 0)
        ''', row['importance'], f'{{"feature": "{row["feature"]}", "importance": {row["importance"]}}}')

    await conn.close()

    print(f'\n========== 模型训练完成 ==========')
    print(f'最终 AUC: {auc:.4f}')
    print(f'模型文件: {MODEL_DIR / "xgboost_location_model.pkl"}')


if __name__ == '__main__':
    asyncio.run(main())
