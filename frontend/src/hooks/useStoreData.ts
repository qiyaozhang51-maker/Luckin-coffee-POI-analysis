/**
 * @fileoverview 门店数据 Hooks —— 时间轴趋势 + 热力图分布
 *
 * 职责：
 * - useTimeline：获取门店月度新增/累计数据，用于绘制增长趋势图
 * - useHeatmap：获取门店地理坐标数据，用于在地图上渲染热力图/散点图
 *
 * 两者均支持按品牌和城市筛选，数据通过 useApi 自动管理和缓存。
 */

import { useMemo } from 'react';
import { useApi } from './useApi';

/**
 * 时间轴数据点
 * 每个数据点代表某月某品牌的门店增长情况
 */
interface TimelinePoint {
  /** 月份（YYYY-MM 格式） */
  month: string;
  /** 当月新增门店数 */
  new_stores: number;
  /** 截至当月的累计门店数 */
  cumulative_stores: number;
  /** 品牌标识 */
  brand: string;
}

/**
 * 热力图数据
 * 包装 GeoJSON 坐标数组，附带品牌和地理筛选信息
 */
interface HeatmapData {
  /** GeoJSON 坐标数据（FeatureCollection 或坐标数组） */
  geojson: any;
  /** 坐标点总数 */
  count: number;
  /** 品牌标识 */
  brand: string;
  /** 筛选城市（null 表示全国） */
  city: string | null;
  /** 筛选年份（null 表示不限年份） */
  year: number | null;
}

/**
 * 门店时间轴 Hook
 *
 * 获取指定品牌（和城市）的门店月度增长数据，并计算年均增长率。
 *
 * @param brand - 品牌标识，默认 'luckin'
 * @param city - 可选的城市筛选，不传则返回全国数据
 * @returns timeline - 月度时间序列数据
 * @returns avgGrowthRate - 年均增长率（百分比），比较第一年和最后一年的新增门店数
 * @returns loading - 加载状态
 * @returns error - 错误信息
 */
export function useTimeline(brand: string = 'luckin', city?: string) {
  const params: Record<string, any> = { brand };
  if (city) params.city = city;

  const { data, loading, error } = useApi<TimelinePoint[]>(
    '/api/stores/timeline/data',
    { params }
  );

  /**
   * 计算年均增长率
   * 逻辑：取前 12 个月的新增总和与后 12 个月的新增总和比较
   * useMemo 缓存：仅在 data 变化时重新计算
   */
  const avgGrowthRate = useMemo(() => {
    if (!data || data.length <= 12) return 0;
    const firstYear = data.slice(0, 12).reduce((s, t) => s + t.new_stores, 0);
    const lastYear = data.slice(-12).reduce((s, t) => s + t.new_stores, 0);
    return lastYear > 0 ? Math.round(((lastYear - firstYear) / firstYear) * 100) : 0;
  }, [data]);

  return { timeline: data || [], avgGrowthRate, loading, error };
}

/**
 * 门店热力图 Hook
 *
 * 获取指定品牌、年份和城市下的门店地理坐标数据，用于地图可视化。
 *
 * @param brand - 品牌标识，默认 'luckin'
 * @param year - 可选的年份筛选
 * @param city - 可选的城市筛选
 * @returns heatmap - 热力图数据（GeoJSON 坐标）
 * @returns loading - 加载状态
 * @returns error - 错误信息
 */
export function useHeatmap(brand: string = 'luckin', year?: number, city?: string) {
  const params: Record<string, any> = { brand };
  if (year) params.year = year;
  if (city) params.city = city;

  const { data, loading, error } = useApi<HeatmapData>(
    '/api/stores/heatmap/data',
    { params }
  );

  return { heatmap: data, loading, error };
}
