"""Gate-line person filter: keep only persons whose body is on the driver side.

Pure functions shared by the video player and the test harness. A "gate line"
is drawn at the door threshold; only persons with at least one body anchor
(bbox bottom-center, hip midpoint) on the driver side are considered for
detection. The arms are deliberately excluded — the driver reaches across the
line to make the confirmation gestures.
"""

import math

import numpy as np

HIP_LEFT = 11
HIP_RIGHT = 12


def signed_dist_to_line(pts, p):
    """Signed perpendicular distance (px) from point ``p`` to line ``pts=[A, B]``.

    Positive on one side of the directed line, negative on the other.
    Multiply by the gate's ``inside_side`` flag to decide "driver side".
    """
    (ax, ay), (bx, by) = pts[0], pts[1]
    px, py = p
    dx, dy = bx - ax, by - ay
    seg = math.hypot(dx, dy)
    if seg < 1e-6:
        return 0.0
    return (dx * (py - ay) - dy * (px - ax)) / seg


def person_anchors(box_xyxy, kp_xy, kp_conf, conf_threshold):
    """Body anchors for one person: bbox bottom-center + (optional) hip midpoint.

    ``box_xyxy``: ``(x1, y1, x2, y2)``. ``kp_xy``/``kp_conf``: ``(17, 2)``/``(17,)``
    numpy arrays (COCO 17-keypoint convention).
    """
    x1, y1, x2, y2 = box_xyxy
    anchors = [((x1 + x2) / 2.0, y2)]  # bbox bottom-center = "where the person stands"
    if (kp_conf[HIP_LEFT] > conf_threshold and kp_conf[HIP_RIGHT] > conf_threshold
            and kp_xy[HIP_LEFT][1] > 0 and kp_xy[HIP_RIGHT][1] > 0):
        anchors.append(((kp_xy[HIP_LEFT][0] + kp_xy[HIP_RIGHT][0]) / 2.0,
                        (kp_xy[HIP_LEFT][1] + kp_xy[HIP_RIGHT][1]) / 2.0))
    return anchors


def select_person_idx(boxes_xyxy, boxes_conf, kp_xy, kp_conf, gate_pts, gate_side, *,
                      margin=12, conf_threshold=0.5, last_idx=None):
    """Choose the person to treat as the driver, or None if none qualifies.

    Args:
        boxes_xyxy: ``(N, 4)`` numpy bboxes.
        boxes_conf: ``(N,)`` numpy bbox confidences.
        kp_xy / kp_conf: ``(N, 17, 2)`` / ``(N, 17)`` numpy keypoints.
        gate_pts: ``[A, B]`` gate line, or None to disable the gate.
        gate_side: ``+1`` / ``-1`` — which side of the line counts as inside.
        margin: dead-band tolerance in px around the line.
        conf_threshold: minimum keypoint confidence to trust hip anchors.
        last_idx: person index selected on the previous frame (hysteresis).

    Returns:
        Selected person index, or None (drop all persons).
    """
    n = len(boxes_xyxy)
    if n == 0:
        return None

    if gate_pts is None:
        if n == 1:
            return 0
        return int(np.argmax(boxes_conf))

    # Gate active: a person is inside if ANY body anchor is on the driver side
    # (within ``margin`` of the line). Among inside persons keep highest conf.
    best_d = []
    for i in range(n):
        anchors = person_anchors(boxes_xyxy[i], kp_xy[i], kp_conf[i], conf_threshold)
        d = max(signed_dist_to_line(gate_pts, a) * gate_side for a in anchors)
        best_d.append(d)

    inside = [i for i, d in enumerate(best_d) if d > -margin]
    if inside:
        return int(max(inside, key=lambda i: boxes_conf[i]))

    # Nothing clearly inside: stick to the previous selection if it is still
    # near the line (hysteresis prevents flip-flopping on the boundary).
    if last_idx is not None and last_idx < n and best_d[last_idx] > -2 * margin:
        return int(last_idx)

    return None
