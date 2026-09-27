/**
 * RoundCharts — small dependency-free canvas charts for the Rounds page:
 * a score-over-time trend line, and an average-score-by-course bar chart.
 */
(function (global) {
  function drawScoreTrend(canvas, rounds) {
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    if (!rounds.length) {
      drawEmptyMessage(ctx, W, H, 'No rounds yet');
      return;
    }

    const sorted = [...rounds].sort(
      (a, b) => new Date(a.played_at) - new Date(b.played_at)
    );

    const padding = { top: 20, bottom: 30, left: 44, right: 16 };
    const usableW = W - padding.left - padding.right;
    const usableH = H - padding.top - padding.bottom;

    const scores = sorted.map((r) => r.total_score);
    const minScore = Math.min(...scores);
    const maxScore = Math.max(...scores);
    const scorePad = Math.max(2, Math.round((maxScore - minScore) * 0.15));
    const yMin = minScore - scorePad;
    const yMax = maxScore + scorePad;

    function xFor(i) {
      return sorted.length === 1
        ? padding.left + usableW / 2
        : padding.left + (i / (sorted.length - 1)) * usableW;
    }
    function yFor(score) {
      return (
        padding.top + usableH - ((score - yMin) / (yMax - yMin)) * usableH
      );
    }

    // Horizontal gridlines + labels
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.25)';
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    const gridLines = 4;
    for (let i = 0; i <= gridLines; i++) {
      const score = yMin + ((yMax - yMin) * i) / gridLines;
      const y = yFor(score);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(W - padding.right, y);
      ctx.stroke();
      ctx.fillText(Math.round(score), padding.left - 8, y + 4);
    }

    // Line
    ctx.beginPath();
    sorted.forEach((r, i) => {
      const x = xFor(i);
      const y = yFor(r.total_score);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = '#16a34a';
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    ctx.stroke();

    // Points
    sorted.forEach((r, i) => {
      const x = xFor(i);
      const y = yFor(r.total_score);
      ctx.beginPath();
      ctx.arc(x, y, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = '#16a34a';
      ctx.fill();
    });

    // X-axis: first and last date only, to avoid clutter
    ctx.fillStyle = '#64748b';
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(sorted[0].played_at, padding.left, H - 8);
    ctx.textAlign = 'right';
    ctx.fillText(
      sorted[sorted.length - 1].played_at,
      W - padding.right,
      H - 8
    );
  }

  function drawAvgByCourse(canvas, courseStats) {
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    if (!courseStats.length) {
      drawEmptyMessage(ctx, W, H, 'No rounds yet');
      return;
    }

    const padding = { top: 10, bottom: 10, left: 10, right: 50 };
    const rowHeight = Math.min(34, (H - padding.top - padding.bottom) / courseStats.length);
    const maxAvg = Math.max(...courseStats.map((c) => c.avg_score));
    const usableW = W - padding.left - padding.right - 130; // leave room for course name labels

    ctx.font = '12px system-ui, sans-serif';
    courseStats.forEach((c, i) => {
      const y = padding.top + i * rowHeight;
      const barW = (c.avg_score / maxAvg) * usableW;

      ctx.fillStyle = '#334155';
      ctx.textAlign = 'left';
      ctx.fillText(truncate(c.course_name, 18), padding.left, y + rowHeight * 0.6);

      ctx.fillStyle = '#bbf7d0';
      ctx.fillRect(padding.left + 130, y + rowHeight * 0.2, barW, rowHeight * 0.5);

      ctx.fillStyle = '#166534';
      ctx.textAlign = 'left';
      ctx.fillText(
        c.avg_score.toFixed(1),
        padding.left + 130 + barW + 6,
        y + rowHeight * 0.6
      );
    });
  }

  function truncate(str, n) {
    return str.length > n ? str.slice(0, n - 1) + '…' : str;
  }

  function drawEmptyMessage(ctx, W, H, msg) {
    ctx.fillStyle = '#94a3b8';
    ctx.font = '13px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(msg, W / 2, H / 2);
  }

  global.RoundCharts = { drawScoreTrend, drawAvgByCourse };
})(window);
