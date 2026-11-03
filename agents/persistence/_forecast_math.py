"""预测数学：线性回归 / 移动平均 / 指数平滑拟合。"""


class ForecastMathMixin:
    """ForecastMathMixin —— 由 agents/persistence.py 拆分而来（iter #77）。"""

    # ============================================
    # iter #28: forecast method fitters
    # Each returns: {slope, forecast_values_fn, ci_half, r_squared,
    #                trend, residual_std, method_meta}
    # ============================================
    def _fit_linear(self, sim_days, values):
        """Linear regression (iter #26 default). y = slope * x + intercept."""
        import numpy as np
        slope, intercept = np.polyfit(sim_days, values, 1)
        # Compute R²
        y_pred_hist = slope * sim_days + intercept
        ss_res = float(np.sum((values - y_pred_hist) ** 2))
        ss_tot = float(np.sum((values - np.mean(values)) ** 2))
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
        # Residual std
        residuals = values - y_pred_hist
        residual_std = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
        ci_half = 1.96 * residual_std
        # Trend
        week_change = slope * 7
        if abs(week_change) < 0.05 * abs(intercept) if intercept else abs(week_change) < 1:
            trend = "flat"
        elif week_change > 0:
            trend = "up"
        else:
            trend = "down"
        return {
            "slope": float(slope),
            "forecast_values_fn": lambda x: slope * x + intercept,
            "ci_half": ci_half,
            "r_squared": r_squared,
            "trend": trend,
            "residual_std": residual_std,
            "method_meta": {"intercept": round(float(intercept), 4)},
        }

    def _fit_moving_average(self, sim_days, values):
        """Moving average (iter #28). Forecast = mean of all values, flat line."""
        import numpy as np
        window_mean = float(np.mean(values))
        window_std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
        ci_half = 1.96 * window_std
        # No trend (flat) by definition
        return {
            "slope": 0.0,
            "forecast_values_fn": lambda x: np.full_like(x, window_mean, dtype=float),
            "ci_half": ci_half,
            "r_squared": 0.0,  # Not applicable for moving average
            "trend": "flat",
            "residual_std": window_std,
            "method_meta": {"window_mean": round(window_mean, 2), "window_n": int(len(values))},
        }

    def _fit_exponential_smoothing(self, sim_days, values):
        """Exponential smoothing (iter #28, simple SES, alpha=0.3).

        forecast = final smoothed level (constant for all future points)
        适合近期值更重要 / 不需趋势外推 的场景
        """
        import numpy as np
        alpha = 0.3  # smoothing constant (0-1, 越小越平滑)
        # Compute SES for history
        n = len(values)
        if n == 0:
            smoothed_levels = np.array([0.0])
        else:
            smoothed_levels = np.zeros(n)
            smoothed_levels[0] = float(values[0])
            for i in range(1, n):
                smoothed_levels[i] = alpha * float(values[i]) + (1 - alpha) * smoothed_levels[i - 1]
        final_level = float(smoothed_levels[-1])
        # Residual std based on one-step-ahead errors
        one_step_errors = []
        for i in range(1, n):
            pred = smoothed_levels[i - 1]
            one_step_errors.append(float(values[i]) - pred)
        residual_std = float(np.std(one_step_errors, ddof=1)) if len(one_step_errors) > 1 else 0.0
        ci_half = 1.96 * residual_std
        return {
            "slope": 0.0,
            "forecast_values_fn": lambda x: np.full_like(x, final_level, dtype=float),
            "ci_half": ci_half,
            "r_squared": 0.0,
            "trend": "flat",
            "residual_std": residual_std,
            "method_meta": {"alpha": alpha, "final_level": round(final_level, 2)},
        }
