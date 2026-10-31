"""Time estimates from the speed this machine has actually shown."""

from statistics import median

from spleeter_gui.core.models import Job

# In a short section the model's loading weighs too much for its speed to say anything.
MIN_SECONDS = 120.0
_MAX_SAMPLES = 5


def estimate_seconds(job: Job, past_runs: list[Job], speed_keys: set[str]) -> float | None:
    """How long `job` should take, from runs of the same model, most recent first."""
    if job.effective_duration < MIN_SECONDS:
        return None
    ratios = [
        run.elapsed_s / run.effective_duration
        for run in past_runs
        if run.elapsed_s
        and run.effective_duration >= MIN_SECONDS
        and all(run.options.get(key) == job.options.get(key) for key in speed_keys)
    ]
    if not ratios:
        return None
    # The median, because the same separation can take almost twice as long when the CPU is hot.
    return median(ratios[:_MAX_SAMPLES]) * job.effective_duration
