/**
 * @fileoverview 品牌对比 Hook
 *
 * 职责：
 * - 获取瑞幸与星巴克的双品牌对比数据（门店数、共址率、平均距离）
 * - 获取各城市层级（一线/新一线/二线/其他）的门店渗透数据
 * - 支撑 ComparisonPage 的品牌对比分析和策略解读
 *
 * 同时发起两个 API 请求，合并为统一的 loading/error 状态。
 */

import { useApi } from './useApi';

/**
 * 品牌门店数量统计
 */
interface BrandCount {
  /** 品牌标识 */
  brand: string;
  /** 该品牌门店总数 */
  count: number;
}

/**
 * 共址分析数据
 * 衡量瑞幸门店在星巴克附近的密集程度
 */
interface CoLocation {
  /** 参与统计的瑞幸门店总数 */
  total_luckin: number;
  /** 距离星巴克 200m 内的瑞幸门店数 */
  within_200m_of_starbucks: number;
  /** 共址率 = within_200m_of_starbucks / total_luckin */
  co_location_rate: number;
}

/**
 * 品牌对比综合数据
 */
interface ComparisonData {
  /** 各品牌门店数量 */
  brand_counts: BrandCount[];
  /** 瑞幸门店到最近星巴克的平均距离（米），null 表示无数据 */
  avg_nearest_starbucks_m: number | null;
  /** 共址分析数据，null 表示无数据 */
  co_location: CoLocation | null;
}

/**
 * 城市层级门店统计
 * 按城市层级维度汇总各品牌的门店渗透情况
 */
interface CityTierStat {
  /** 城市层级标签（如"一线"、"新一线"） */
  tier_label: string;
  /** 城市层级编码（1=一线, 2=新一线, 3=二线, 4=其他） */
  tier: number;
  /** 品牌标识 */
  brand: string;
  /** 已进入的城市数量 */
  cities_entered: number;
  /** 门店总数 */
  total_stores: number;
}

/**
 * 品牌对比 Hook
 *
 * 同时获取品牌对比数据和城市层级统计数据。
 *
 * @param city - 可选的城市筛选（null 表示全国范围对比）
 * @returns comparison - 品牌对比数据（门店数、共址率、平均距离）
 * @returns cityTierStats - 各城市层级的门店渗透统计
 * @returns loading - 加载状态
 * @returns error - 错误信息
 * @returns refetch - 手动重新获取
 */
export function useComparison(city?: string) {
  const params = city ? { city } : undefined;

  /** 品牌对比 API（支持城市筛选） */
  const comparison = useApi<ComparisonData>(
    '/api/analysis/brand-comparison',
    { params }
  );

  /** 城市层级统计 API（全国范围，无需城市筛选） */
  const tierStats = useApi<CityTierStat[]>(
    '/api/analysis/city-tiers-stats'
  );

  return {
    comparison: comparison.data,
    cityTierStats: tierStats.data || [],
    loading: comparison.loading || tierStats.loading,
    error: comparison.error || tierStats.error,
    refetch: () => { comparison.refetch(); tierStats.refetch(); },
  };
}
