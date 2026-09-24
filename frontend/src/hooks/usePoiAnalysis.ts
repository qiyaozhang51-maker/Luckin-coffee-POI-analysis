/**
 * @fileoverview POI 关联分析 Hook
 *
 * 职责：
 * - 获取门店周边各 POI 类别的统计分布（平均值、标准差、最大/最小值）
 * - 获取 Lift 分值，衡量各 POI 类别在门店附近的富集程度
 * - Lift > 1 表示该 POI 类型在门店周边比随机分布更密集，是选址的潜在吸引因素
 *
 * 同时发起两个 API 请求（store-poi-correlation 和 lift-scores），
 * 合并 loading/error 状态为统一出口。
 */

import { useApi } from './useApi';

/**
 * POI 关联统计
 * 描述某个缓冲半径下，某类 POI 在所有门店周边的统计分布
 */
interface PoiCorrelation {
  /** POI 类别名称（如"办公"、"交通"、"商业"） */
  category: string;
  /** 缓冲半径（米） */
  radius: number;
  /** 所有门店周边该类别 POI 的平均数量 */
  avg_count: number;
  /** 标准差：反映门店间 POI 分布的离散程度 */
  stddev: number;
  /** 所有门店中该类别 POI 的最小值 */
  min_count: number;
  /** 所有门店中该类别 POI 的最大值 */
  max_count: number;
  /** 参与统计的门店数量 */
  store_count: number;
}

/**
 * Lift 分值
 * 衡量 POI 类别在门店周边的富集程度，相对于随机分布的倍数
 */
interface LiftScore {
  /** POI 类别名称 */
  category: string;
  /** 门店周边该 POI 的平均数量 */
  avg_near_store: number;
  /** 随机采样点该 POI 的平均数量（基准线） */
  avg_random: number;
  /** Lift = avg_near_store / avg_random，>1 表示富集 */
  lift: number;
}

/**
 * POI 关联分析 Hook
 *
 * 同时发起两个 API 请求获取 POI 统计数据：
 * - store-poi-correlation：各 POI 类别的统计分布
 * - lift-scores：各 POI 类别的富集程度（Lift 值）
 *
 * @param brand - 品牌标识，默认 'luckin'
 * @param radius - 缓冲半径（米），默认 500
 * @param city - 可选的城市筛选
 * @returns correlation - POI 关联统计数据
 * @returns liftData - Lift 分值数据
 * @returns loading - 两个请求中任一加载即为 true
 * @returns error - 两个请求中任一错误
 * @returns refetch - 手动重新获取两个数据源
 */
export function usePoiAnalysis(
  brand: string = 'luckin',
  radius: number = 500,
  city?: string,
) {
  const params: Record<string, any> = { brand, radius };
  if (city) params.city = city;

  /** POI 关联统计 API */
  const correlation = useApi<PoiCorrelation[]>(
    '/api/analysis/store-poi-correlation',
    { params }
  );

  /** Lift 分值 API */
  const lift = useApi<LiftScore[]>(
    '/api/analysis/lift-scores',
    { params }
  );

  return {
    correlation: correlation.data || [],
    liftData: lift.data || [],
    loading: correlation.loading || lift.loading,
    error: correlation.error || lift.error,
    refetch: () => { correlation.refetch(); lift.refetch(); },
  };
}
