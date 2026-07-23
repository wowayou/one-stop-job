import { useEffect, useState } from "react";

export function PaginationControls({
  page,
  total,
  pageSize,
  onPage
}: {
  page: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const start = total ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(total, page * pageSize);
  const [pageInput, setPageInput] = useState(String(page));
  useEffect(() => {
    setPageInput(String(page));
  }, [page]);

  function commitPage() {
    const parsed = Number.parseInt(pageInput, 10);
    const next = Number.isFinite(parsed) ? Math.min(pageCount, Math.max(1, parsed)) : page;
    setPageInput(String(next));
    onPage(next);
  }

  return (
    <div className="pagination">
      <span>
        {start}-{end} / {total}
      </span>
      <button className="small-action" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1}>
        上一页
      </button>
      <span>
        {page} / {pageCount}
      </span>
      <label className="page-jump">
        跳至
        <input
          value={pageInput}
          type="number"
          min={1}
          max={pageCount}
          onChange={(event) => setPageInput(event.target.value)}
          onBlur={commitPage}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commitPage();
            }
          }}
        />
      </label>
      <button className="small-action" onClick={() => onPage(Math.min(pageCount, page + 1))} disabled={page >= pageCount}>
        下一页
      </button>
    </div>
  );
}
