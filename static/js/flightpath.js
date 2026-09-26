/**
 * DiscFlight — approximates a disc golf flight path from PDGA-style flight
 * numbers (Speed, Glide, Turn, Fade). This is a visual approximation, not a
 * physics simulation: it's meant to give an intuitive sense of a disc's
 * shape (how much it turns early, how hard it fades late), calibrated
 * roughly against typical amateur throwing distances.
 *
 * Coordinate system while computing the path: feet, thrower at (0, 0),
 * "forward" = distance downrange, "lateral" = sideways drift (positive =
 * right of the thrower for a RHBH throw).
 */
(function (global) {
  const MIRROR_BY_STYLE = {
    RHBH: 1,
    LHFH: 1,
    RHFH: -1,
    LHBH: -1,
  };

  function computePath(disc, opts) {
    opts = opts || {};
    const power = opts.power || 1;
    const mirror = MIRROR_BY_STYLE[opts.throwStyle] ?? 1;

    const speed = disc.speed ?? 5;
    const glide = disc.glide ?? 4;
    const turn = disc.turn ?? 0;
    const fade = disc.fade ?? 2;

    const maxDistance = (150 + speed * 18 + glide * 10) * power;

    const turnScale = 18;
    const fadeScale = 22;

    const points = [];
    const STEPS = 60;
    for (let i = 0; i <= STEPS; i++) {
      const t = i / STEPS;

      // Forward progress eases out — the disc covers ground fastest early,
      // then floats (glide) toward the end of the flight.
      const forward = maxDistance * Math.pow(t, 0.9);

      // Turn: strongest in the early-to-mid flight (high speed phase).
      const turnWeight = Math.sin(Math.PI * Math.pow(t, 0.6));
      // Fade: builds toward the end of the flight as the disc slows down.
      const fadeWeight = Math.pow(t, 2.2);

      const lateral =
        mirror *
        (-turn * turnScale * turnWeight - fade * fadeScale * fadeWeight);

      points.push({ forward, lateral });
    }
    return { points, maxDistance };
  }

  function drawPathOnCanvas(canvas, disc, opts, style) {
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const { points, maxDistance } = computePath(disc, opts);

    const padding = style.padding;
    const maxLateral = Math.max(
      style.minLateralSpan,
      ...points.map((p) => Math.abs(p.lateral))
    );

    const usableH = H - padding.top - padding.bottom;
    const usableW = W - padding.left - padding.right;

    const scaleY = usableH / maxDistance;
    const scaleX = (usableW / 2) / (maxLateral * 1.15);
    const scale = Math.min(scaleX, scaleY);

    const originX = W / 2;
    const originY = H - padding.bottom;

    function toCanvas(p) {
      return {
        x: originX + p.lateral * scale,
        y: originY - p.forward * scale,
      };
    }

    if (style.grid) {
      drawGrid(ctx, W, H, originX, originY, scale, maxDistance, style);
    }

    // Path
    ctx.beginPath();
    points.forEach((p, i) => {
      const c = toCanvas(p);
      if (i === 0) ctx.moveTo(c.x, c.y);
      else ctx.lineTo(c.x, c.y);
    });
    ctx.strokeStyle = disc.color || '#3b82f6';
    ctx.lineWidth = style.lineWidth;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();

    // Start marker (thrower)
    const start = toCanvas(points[0]);
    ctx.beginPath();
    ctx.arc(start.x, start.y, style.markerRadius, 0, Math.PI * 2);
    ctx.fillStyle = style.startColor;
    ctx.fill();

    // End marker (landing)
    const end = toCanvas(points[points.length - 1]);
    ctx.beginPath();
    ctx.arc(end.x, end.y, style.markerRadius, 0, Math.PI * 2);
    ctx.fillStyle = disc.color || '#3b82f6';
    ctx.fill();

    if (style.showDistanceLabel) {
      ctx.fillStyle = style.labelColor;
      ctx.font = style.labelFont;
      ctx.textAlign = 'center';
      ctx.fillText(
        Math.round(maxDistance) + ' ft',
        end.x,
        end.y - style.markerRadius - 8
      );
    }
  }

  function drawGrid(ctx, W, H, originX, originY, scale, maxDistance, style) {
    ctx.save();
    ctx.strokeStyle = style.gridColor;
    ctx.lineWidth = 1;
    ctx.font = style.gridFont;
    ctx.fillStyle = style.gridTextColor;
    ctx.textAlign = 'left';

    const stepFeet = 50;
    for (let d = 0; d <= maxDistance + stepFeet; d += stepFeet) {
      const y = originY - d * scale;
      if (y < style.padding.top) break;
      ctx.beginPath();
      ctx.moveTo(style.padding.left * 0.4, y);
      ctx.lineTo(W - style.padding.right * 0.4, y);
      ctx.stroke();
      ctx.fillText(d + 'ft', 4, y - 3);
    }

    // Center line (thrower's straight line)
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(originX, style.padding.top);
    ctx.lineTo(originX, originY);
    ctx.strokeStyle = style.centerLineColor;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  }

  function drawFull(canvas, disc, opts) {
    drawPathOnCanvas(canvas, disc, opts, {
      padding: { top: 30, bottom: 20, left: 40, right: 20 },
      minLateralSpan: 40,
      lineWidth: 4,
      markerRadius: 5,
      startColor: '#111827',
      grid: true,
      gridColor: 'rgba(148, 163, 184, 0.25)',
      gridTextColor: '#94a3b8',
      gridFont: '11px system-ui, sans-serif',
      centerLineColor: 'rgba(148, 163, 184, 0.4)',
      showDistanceLabel: true,
      labelColor: '#334155',
      labelFont: '600 13px system-ui, sans-serif',
    });
  }

  function drawMini(canvas, disc) {
    drawPathOnCanvas(canvas, disc, { throwStyle: 'RHBH', power: 1 }, {
      padding: { top: 10, bottom: 10, left: 16, right: 16 },
      minLateralSpan: 30,
      lineWidth: 3,
      markerRadius: 3,
      startColor: '#cbd5e1',
      grid: false,
      showDistanceLabel: false,
    });
  }

  global.DiscFlight = { drawFull, drawMini, computePath };
})(window);
