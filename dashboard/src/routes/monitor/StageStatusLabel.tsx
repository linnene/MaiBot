import { useEffect, useState } from 'react'

import type { StageStatusInfo } from './use-maisaka-monitor'

export function StageStatusLabel({ status }: { status: StageStatusInfo }) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  const waitUntil = status.agentState === 'wait' ? status.waitUntil : undefined

  useEffect(() => {
    if (waitUntil === undefined) return
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [waitUntil])

  // 使用后端的等待截止时间，刷新或重新进入页面后仍显示真实剩余时间。
  if (waitUntil !== undefined) {
    const remainingSeconds = Math.max(0, Math.ceil(waitUntil - now))
    if (status.interactionKind === 'deliberate_wait') {
      const plannedSeconds =
        status.plannedDurationSeconds === undefined
          ? null
          : Math.max(0, Math.round(status.plannedDurationSeconds))
      if (remainingSeconds > 0) {
        return (
          <>
            主动延后(剩余 {remainingSeconds}s
            {plannedSeconds === null ? '' : ` / 计划 ${plannedSeconds}s`})
          </>
        )
      }
      return <>{plannedSeconds === null ? '主动延后结束，即将继续' : `主动延后(计划 ${plannedSeconds}s，即将继续)`}</>
    }
    return <>{remainingSeconds > 0 ? `等待(${remainingSeconds}s)` : '等待结束，即将继续'}</>
  }
  return <>{status.stage === '等待消息' ? '空闲' : status.stage || '未知阶段'}</>
}
