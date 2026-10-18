// The rows behind an answer (mockup: rowsTable): TanStack Table for the sorting; the table scrolls
// inside its card (sticky headers), numeric columns are right-aligned, list cells are small chips.
// Headers are sort buttons: ascending -> descending -> the server's order.

import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import { useMemo, useState } from 'react';
import type { AskCell } from '../../../api/types';
import { cellText, colKinds, colName, compareCells, type ColKind } from '../../../lib/ask';

const LIST_SHOWN = 8;

function Cell({ v }: { v: AskCell | undefined }) {
  if (Array.isArray(v)) {
    return (
      <span className="ask-li">
        {v.slice(0, LIST_SHOWN).map((x, i) => (
          <span key={i}>{cellText(x)}</span>
        ))}
        {v.length > LIST_SHOWN ? (
          <span className="more" title={v.slice(LIST_SHOWN).map(cellText).join(', ')}>
            +{v.length - LIST_SHOWN}
          </span>
        ) : null}
      </span>
    );
  }
  return <>{cellText(v)}</>;
}

const tdClass = (v: AskCell | undefined, kind: ColKind) =>
  v == null ? 'null' : Array.isArray(v) ? '' : kind === 'num' ? 'r num' : String(v).length > 40 ? 'txt long' : 'txt';

export function RowsTable({
  columns,
  rows,
  rowCount,
  truncated,
}: {
  columns: string[];
  rows: AskCell[][];
  rowCount: number;
  truncated: boolean;
}) {
  const kinds = useMemo(() => colKinds(columns, rows), [columns, rows]);
  const defs = useMemo<ColumnDef<AskCell[], AskCell | undefined>[]>(
    () =>
      columns.map((c, i) => ({
        id: `c${i}`,
        header: colName(c),
        // null -> undefined so `sortUndefined: 'last'` keeps empty cells last in both directions
        accessorFn: (r) => r[i] ?? undefined,
        sortingFn: (a, b, id) => compareCells(a.getValue<AskCell | undefined>(id), b.getValue<AskCell | undefined>(id)),
        sortUndefined: 'last',
        meta: { kind: kinds[i] },
      })),
    [columns, kinds],
  );
  const [sorting, setSorting] = useState<SortingState>([]);
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table's hook returns functions the compiler can't memoise
  const table = useReactTable({
    data: rows,
    columns: defs,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    sortDescFirst: false,
    enableMultiSort: false,
  });
  const count = `Showing ${rows.length} of ${rowCount} row${rowCount === 1 ? '' : 's'}${
    truncated ? ` · more rows existed than the ${rows.length}-row cap` : ''
  }`;
  return (
    <>
      <div className="ask-rows-h">
        <h3>The rows</h3>
        <p>{count}{rows.length > 1 ? ' · click a header to sort' : ''}</p>
      </div>
      <div className="ask-tablewrap" tabIndex={0} role="region" aria-label="The rows behind the answer">
        <table className="tbl ask-tbl">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const s = h.column.getIsSorted();
                  const kind = (h.column.columnDef.meta as { kind: ColKind } | undefined)?.kind;
                  return (
                    <th
                      key={h.id}
                      scope="col"
                      className={kind === 'num' ? 'r' : undefined}
                      aria-sort={s === 'asc' ? 'ascending' : s === 'desc' ? 'descending' : 'none'}
                    >
                      <button type="button" onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        <span className="arr" aria-hidden="true">
                          {s === 'asc' ? '▲' : s === 'desc' ? '▼' : ''}
                        </span>
                      </button>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((r) => (
              <tr key={r.id}>
                {r.getVisibleCells().map((c, i) => {
                  const v = c.getValue<AskCell | undefined>();
                  return (
                    <td key={c.id} className={tdClass(v, kinds[i])}>
                      <Cell v={v} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
