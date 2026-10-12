import { init, use } from 'echarts/core'
import { LineChart, BarChart, CandlestickChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
use([LineChart, BarChart, CandlestickChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, CanvasRenderer])
export const mountChart = element => init(element, null, { renderer: 'canvas' })
