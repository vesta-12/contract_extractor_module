from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median

from contract_extractor.models import (
    BoundingBox,
    LayoutLine,
    OCRDocument,
    OCRPage,
    OCRWord,
)


@dataclass(frozen=True, slots=True)
class LineBuilderConfig:
    min_vertical_overlap: float = 0.45

    max_center_distance_factor: float = 0.75

    min_center_tolerance: float = 0.003

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_vertical_overlap <= 1.0:
            raise ValueError(
                "min_vertical_overlap должен находиться "
                "в диапазоне от 0.0 до 1.0"
            )

        if self.max_center_distance_factor <= 0:
            raise ValueError(
                "max_center_distance_factor должен быть больше 0"
            )

        if self.min_center_tolerance < 0:
            raise ValueError(
                "min_center_tolerance не может быть отрицательным"
            )


@dataclass(slots=True)
class _LineCluster:

    region: str | None
    words: list[OCRWord] = field(default_factory=list)
    bbox: BoundingBox | None = None

    def add_word(self, word: OCRWord) -> None:
        if word.region != self.region:
            raise ValueError(
                f"нельзя добавить слово региона {word.region} "
                f"в кластер региона {self.region}"
            )

        self.words.append(word)

        if self.bbox is None:
            self.bbox = word.bbox
        else:
            self.bbox = self.bbox.union(word.bbox)

    @property
    def median_center_y(self) -> float:
        if not self.words:
            raise ValueError(
                "нельзя вычислить центр пустого кластера"
            )

        return median(
            word.bbox.center_y
            for word in self.words
        )

    @property
    def median_height(self) -> float:
        if not self.words:
            raise ValueError(
                "нельзя вычислить высоту пустого кластера"
            )

        return median(
            word.bbox.height
            for word in self.words
        )

    def compatibility_score(
        self,
        word: OCRWord,
        config: LineBuilderConfig,
    ) -> float | None:

        if self.bbox is None:
            return 0.0

        vertical_overlap = (
            self.bbox.vertical_overlap_ratio(
                word.bbox
            )
        )

        center_distance = abs(
            self.median_center_y
            - word.bbox.center_y
        )

        reference_height = max(
            self.median_height,
            word.bbox.height,
        )

        allowed_center_distance = max(
            config.min_center_tolerance,
            (
                reference_height
                * config.max_center_distance_factor
            ),
        )

        has_enough_overlap = (
            vertical_overlap
            >= config.min_vertical_overlap
        )

        has_close_center = (
            center_distance
            <= allowed_center_distance
        )

        if (
            not has_enough_overlap
            and not has_close_center
        ):
            return None

        normalized_center_distance = (
            center_distance
            / max(reference_height, 1e-9)
        )
        return (
            normalized_center_distance
            - vertical_overlap * 0.25
        )


class LayoutLineBuilder:
    def __init__(
        self,
        config: LineBuilderConfig | None = None,
    ) -> None:
        self.config = config or LineBuilderConfig()

    def build_document(
        self,
        document: OCRDocument,
    ) -> tuple[LayoutLine, ...]:

        lines: list[LayoutLine] = []

        for page in document.pages:
            lines.extend(
                self.build_page(page)
            )

        return tuple(lines)

    def build_by_page(
        self,
        document: OCRDocument,
    ) -> dict[int, tuple[LayoutLine, ...]]:

        return {
            page.number: self.build_page(page)
            for page in document.pages
        }

    def build_page(
        self,
        page: OCRPage,
    ) -> tuple[LayoutLine, ...]:

        words_by_region = self._group_by_region(
            page.words
        )

        clusters: list[_LineCluster] = []

        for region_words in words_by_region.values():
            region_clusters = (
                self._build_region_clusters(
                    region_words
                )
            )

            clusters.extend(region_clusters)

        clusters.sort(
            key=lambda cluster: (
                self._cluster_y(cluster),
                self._cluster_x(cluster),
            )
        )

        lines = tuple(
            self._create_line(
                page_number=page.number,
                line_index=line_index,
                cluster=cluster,
            )
            for line_index, cluster
            in enumerate(clusters)
        )

        return lines

    @staticmethod
    def _group_by_region(
        words: tuple[OCRWord, ...],
    ) -> dict[str | None, list[OCRWord]]:
        groups: dict[
            str | None,
            list[OCRWord],
        ] = defaultdict(list)

        for word in words:
            groups[word.region].append(word)

        return dict(groups)

    def _build_region_clusters(
        self,
        words: list[OCRWord],
    ) -> list[_LineCluster]:

        sorted_words = sorted(
            words,
            key=lambda word: (
                word.bbox.center_y,
                word.bbox.x1,
            ),
        )

        clusters: list[_LineCluster] = []

        for word in sorted_words:
            best_cluster: _LineCluster | None = None
            best_score: float | None = None

            for cluster in clusters:
                score = cluster.compatibility_score(
                    word=word,
                    config=self.config,
                )

                if score is None:
                    continue

                if (
                    best_score is None
                    or score < best_score
                ):
                    best_cluster = cluster
                    best_score = score

            if best_cluster is None:
                new_cluster = _LineCluster(
                    region=word.region
                )
                new_cluster.add_word(word)
                clusters.append(new_cluster)
            else:
                best_cluster.add_word(word)

        return clusters

    @staticmethod
    def _create_line(
        page_number: int,
        line_index: int,
        cluster: _LineCluster,
    ) -> LayoutLine:

        if not cluster.words:
            raise ValueError(
                "нельзя создать строку "
                "из пустого кластера"
            )

        if cluster.bbox is None:
            raise ValueError(
                "у кластера отсутствует bbox"
            )

        sorted_words = tuple(
            sorted(
                cluster.words,
                key=lambda word: (
                    word.bbox.x1,
                    word.index,
                ),
            )
        )

        text = " ".join(
            word.text
            for word in sorted_words
        )

        confidence = sum(
            word.confidence
            for word in sorted_words
        ) / len(sorted_words)

        return LayoutLine(
            id=f"p{page_number}-l{line_index}",
            page=page_number,
            index=line_index,
            region=cluster.region,
            words=sorted_words,
            text=text,
            bbox=cluster.bbox,
            confidence=confidence,
        )

    @staticmethod
    def _cluster_y(
        cluster: _LineCluster,
    ) -> float:
        if cluster.bbox is None:
            return 0.0

        return cluster.bbox.center_y

    @staticmethod
    def _cluster_x(
        cluster: _LineCluster,
    ) -> float:
        if cluster.bbox is None:
            return 0.0

        return cluster.bbox.x1