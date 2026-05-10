package com.codeea.videoeditor.tools

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import com.codeea.videoeditor.R

class TrimHandleView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    interface TrimListener {
        fun onTrimChanged(startMs: Long, endMs: Long)
        fun onTrimCommitted(startMs: Long, endMs: Long)
    }

    var listener: TrimListener? = null

    private val handlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFF6B35.toInt()
    }
    private val selectedRegionPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0x22FF6B35.toInt()
    }
    private val unselectedPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0x99000000.toInt()
    }
    private val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFFFF6B35.toInt()
        style = Paint.Style.STROKE
        strokeWidth = 3f
    }

    private val handleWidth = 24f
    private val handleRadius = 6f

    var durationMs: Long = 10000L
        set(value) {
            field = value
            invalidate()
        }

    var startMs: Long = 0L
        set(value) {
            field = value.coerceIn(0, endMs - minDurationMs)
            invalidate()
        }

    var endMs: Long = durationMs
        set(value) {
            field = value.coerceIn(startMs + minDurationMs, durationMs)
            invalidate()
        }

    var thumbnails: List<Bitmap> = emptyList()
        set(value) {
            field = value
            invalidate()
        }

    private val minDurationMs = 500L
    private var isDraggingStart = false
    private var isDraggingEnd = false
    private var lastTouchX = 0f

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val usableWidth = width - 2 * handleWidth
        val startX = handleWidth + (startMs.toFloat() / durationMs) * usableWidth
        val endX = handleWidth + (endMs.toFloat() / durationMs) * usableWidth

        // Draw thumbnails background
        val thumbPaint = Paint()
        if (thumbnails.isNotEmpty()) {
            val thumbW = usableWidth / thumbnails.size
            thumbnails.forEachIndexed { i, bmp ->
                val left = handleWidth + i * thumbW
                val right = left + thumbW
                canvas.drawBitmap(
                    bmp,
                    android.graphics.Rect(0, 0, bmp.width, bmp.height),
                    RectF(left, 0f, right, height.toFloat()),
                    thumbPaint
                )
            }
        }

        // Dark overlay on unselected regions
        if (startX > handleWidth) {
            canvas.drawRect(handleWidth, 0f, startX, height.toFloat(), unselectedPaint)
        }
        if (endX < width - handleWidth) {
            canvas.drawRect(endX, 0f, width - handleWidth.toFloat(), height.toFloat(), unselectedPaint)
        }

        // Selected region highlight border
        canvas.drawRect(startX, 0f, endX, height.toFloat(), selectedRegionPaint)
        canvas.drawRect(startX, 0f, endX, height.toFloat(), borderPaint)

        // Start handle
        canvas.drawRoundRect(
            RectF(startX - handleWidth / 2, 0f, startX + handleWidth / 2, height.toFloat()),
            handleRadius, handleRadius, handlePaint
        )

        // End handle
        canvas.drawRoundRect(
            RectF(endX - handleWidth / 2, 0f, endX + handleWidth / 2, height.toFloat()),
            handleRadius, handleRadius, handlePaint
        )

        // Handle arrows
        val arrowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = 0xFFFFFFFF.toInt()
            strokeWidth = 3f
            style = Paint.Style.STROKE
            strokeCap = Paint.Cap.ROUND
        }
        val mid = height / 2f
        canvas.drawLine(startX - 4, mid - 8, startX - 4, mid + 8, arrowPaint)
        canvas.drawLine(startX - 4, mid - 8, startX - 10, mid, arrowPaint)
        canvas.drawLine(startX - 4, mid + 8, startX - 10, mid, arrowPaint)

        canvas.drawLine(endX + 4, mid - 8, endX + 4, mid + 8, arrowPaint)
        canvas.drawLine(endX + 4, mid - 8, endX + 10, mid, arrowPaint)
        canvas.drawLine(endX + 4, mid + 8, endX + 10, mid, arrowPaint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        val usableWidth = width - 2 * handleWidth
        val startX = handleWidth + (startMs.toFloat() / durationMs) * usableWidth
        val endX = handleWidth + (endMs.toFloat() / durationMs) * usableWidth
        val touchSlop = handleWidth + 16

        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                lastTouchX = event.x
                isDraggingStart = Math.abs(event.x - startX) < touchSlop
                isDraggingEnd = !isDraggingStart && Math.abs(event.x - endX) < touchSlop
                return isDraggingStart || isDraggingEnd
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = event.x - lastTouchX
                lastTouchX = event.x
                val dMs = (dx / usableWidth * durationMs).toLong()
                if (isDraggingStart) {
                    startMs = (startMs + dMs).coerceIn(0, endMs - minDurationMs)
                    listener?.onTrimChanged(startMs, endMs)
                } else if (isDraggingEnd) {
                    endMs = (endMs + dMs).coerceIn(startMs + minDurationMs, durationMs)
                    listener?.onTrimChanged(startMs, endMs)
                }
                return true
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (isDraggingStart || isDraggingEnd) {
                    listener?.onTrimCommitted(startMs, endMs)
                }
                isDraggingStart = false
                isDraggingEnd = false
            }
        }
        return false
    }
}
