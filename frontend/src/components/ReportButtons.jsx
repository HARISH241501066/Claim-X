import { downloadCaseReport, downloadUnitReport } from '../api'
import { useDownload } from '../lib/useDownload'
import { Button } from './ui'

function Status({ state }) {
  if (!state.message) return null
  return (
    <p role={state.ok ? 'status' : 'alert'} data-testid="download-message" className={`mt-1 text-xs ${state.ok ? 'text-good' : 'text-flag'}`}>
      {state.message}
    </p>
  )
}

export function CaseReportButton({ caseId }) {
  const [state, run] = useDownload()
  return (
    <div>
      <Button disabled={state.busy !== null} onClick={() => run('case', () => downloadCaseReport(caseId))}>
        {state.busy ? 'Preparing…' : 'Download case report (PDF)'}
      </Button>
      <Status state={state} />
    </div>
  )
}

export function UnitReportButtons({ unitId }) {
  const [state, run] = useDownload()
  return (
    <div>
      <div className="flex flex-wrap gap-2">
        <Button disabled={state.busy !== null} onClick={() => run('pdf', () => downloadUnitReport(unitId, 'pdf'))}>
          Download unit report (PDF)
        </Button>
        <Button disabled={state.busy !== null} onClick={() => run('csv', () => downloadUnitReport(unitId, 'csv'))}>
          Download unit report (CSV)
        </Button>
      </div>
      <Status state={state} />
    </div>
  )
}
