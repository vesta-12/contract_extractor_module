from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Sequence

@dataclass(frozen=True, slots=True)
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        coordinates = (self.x1, self.y1, self.x2, self.y2)

        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError(
                f"BoundingBox содержит некорректные координаты: {coordinates}"
            )

        if self.x1 > self.x2:
            raise ValueError(
                f"x1 не может быть больше x2: x1={self.x1}, x2={self.x2}"
            )

        if self.y1 > self.y2:
            raise ValueError(
                f"y1 не может быть больше y2: y1={self.y1}, y2={self.y2}"
            )

    @classmethod
    def from_sequence(cls, coordinates: Sequence[float]) -> BoundingBox:

        if len(coordinates) != 4:
            raise ValueError(
                "для создания BoundingBox необходимо передать "
                "ровно четыре координаты"
            )

        return cls(
            x1=float(coordinates[0]),
            y1=float(coordinates[1]),
            x2=float(coordinates[2]),
            y2=float(coordinates[3]),
        )

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def center(self) -> tuple[float, float]:
        return self.center_x, self.center_y

    def to_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]

    def union(self, other: BoundingBox) -> BoundingBox:
        return BoundingBox(
            x1=min(self.x1, other.x1),
            y1=min(self.y1, other.y1),
            x2=max(self.x2, other.x2),
            y2=max(self.y2, other.y2),
        )

    def intersection(self, other: BoundingBox) -> BoundingBox | None:
        x1 = max(self.x1, other.x1)
        y1 = max(self.y1, other.y1)
        x2 = min(self.x2, other.x2)
        y2 = min(self.y2, other.y2)

        if x1 >= x2 or y1 >= y2:
            return None

        return BoundingBox(
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
        )

    def intersection_area(self, other: BoundingBox) -> float:
        intersection = self.intersection(other)

        if intersection is None:
            return 0.0

        return intersection.area

    def iou(self, other: BoundingBox) -> float:
        intersection_area = self.intersection_area(other)

        if intersection_area == 0:
            return 0.0

        union_area = self.area + other.area - intersection_area

        if union_area <= 0:
            return 0.0

        return intersection_area / union_area

    def intersects(self, other: BoundingBox) -> bool:
        return self.intersection(other) is not None

    def contains_point(self, x: float, y: float) -> bool:
        return (
            self.x1 <= x <= self.x2
            and self.y1 <= y <= self.y2
        )

    def contains_box(self, other: BoundingBox) -> bool:
        return (
            self.x1 <= other.x1
            and self.y1 <= other.y1
            and self.x2 >= other.x2
            and self.y2 >= other.y2
        )

    def horizontal_gap(self, other: BoundingBox) -> float:

        if self.x2 < other.x1:
            return other.x1 - self.x2

        if other.x2 < self.x1:
            return self.x1 - other.x2

        return 0.0

    def vertical_gap(self, other: BoundingBox) -> float:

        if self.y2 < other.y1:
            return other.y1 - self.y2

        if other.y2 < self.y1:
            return self.y1 - other.y2

        return 0.0

    def vertical_overlap_ratio(self, other: BoundingBox) -> float:
        overlap_start = max(self.y1, other.y1)
        overlap_end = min(self.y2, other.y2)
        overlap = max(0.0, overlap_end - overlap_start)

        minimum_height = min(self.height, other.height)

        if minimum_height <= 0:
            return 0.0

        return overlap / minimum_height

    def horizontal_overlap_ratio(self, other: BoundingBox) -> float:
        overlap_start = max(self.x1, other.x1)
        overlap_end = min(self.x2, other.x2)
        overlap = max(0.0, overlap_end - overlap_start)

        minimum_width = min(self.width, other.width)

        if minimum_width <= 0:
            return 0.0

        return overlap / minimum_width

    def expand(
        self,
        horizontal: float = 0.0,
        vertical: float = 0.0,
    ) -> BoundingBox:

        if horizontal < 0 or vertical < 0:
            raise ValueError(
                "значения расширения не могут быть отрицательными"
            )

        return BoundingBox(
            x1=self.x1 - horizontal,
            y1=self.y1 - vertical,
            x2=self.x2 + horizontal,
            y2=self.y2 + vertical,
        )