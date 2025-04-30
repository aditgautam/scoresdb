from sqlalchemy import Column, Integer, String, Float, ForeignKey, Date
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from datetime import date
from collections import defaultdict

Base = declarative_base()

# Represents a competitive season
class Season(Base):
    __tablename__ = "seasons"
    id = Column(Integer, primary_key=True)
    year = Column(Integer, unique=True, nullable=False)
    shows = relationship("Show", back_populates="season")
    # Caption weights per season (e.g., Music Effect=30)
    caption_weights = relationship("CaptionWeight", back_populates="season")

# Defines caption weights for each season, allowing per-season scoring rules
class CaptionWeight(Base):
    __tablename__ = "caption_weights"
    id = Column(Integer, primary_key=True)
    season_id = Column(Integer, ForeignKey("seasons.id"))
    caption = Column(String, nullable=False)
    weight = Column(Float, nullable=False)

    season = relationship("Season", back_populates="caption_weights")

# Host schools or venues
class HostLocation(Base):
    __tablename__ = "hosts"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    city = Column(String)
    state = Column(String)
    shows = relationship("Show", back_populates="host")

# A single competition/show, held at a host school and part of a season
class Show(Base):
    __tablename__ = "shows"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    date = Column(Date, default=date.today)

    season_id = Column(Integer, ForeignKey("seasons.id"))
    host_id = Column(Integer, ForeignKey("hosts.id"))

    season = relationship("Season", back_populates="shows")
    host = relationship("HostLocation", back_populates="shows")
    week = Column(Integer, nullable=False)
    judge_assignments = relationship("JudgeAssignment", back_populates="show")

# Judges and their information (caption assigned per show via JudgeAssignment)
class Judge(Base):
    __tablename__ = "judges"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    assignments = relationship("JudgeAssignment", back_populates="judge")
    caption_scores = relationship("CaptionScore", back_populates="judge")

# Assignment of a judge to a caption for a specific show
class JudgeAssignment(Base):
    __tablename__ = "judge_assignments"
    id = Column(Integer, primary_key=True)
    show_id = Column(Integer, ForeignKey("shows.id"))
    judge_id = Column(Integer, ForeignKey("judges.id"))
    caption = Column(String)

    show = relationship("Show", back_populates="judge_assignments")
    judge = relationship("Judge", back_populates="assignments")

# Classification of a group (e.g. PSW, PIW)
class Classification(Base):
    __tablename__ = "classifications"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    groups = relationship("Group", back_populates="classification")

# Performing groups
class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    home_city = Column(String)

    classification_id = Column(Integer, ForeignKey("classifications.id"))
    classification = relationship("Classification", back_populates="groups")

# A single performance of a group at a show
class Performance(Base):
    __tablename__ = "performances"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    show_id = Column(Integer, ForeignKey("shows.id"))
    total_score = Column(Float)
    placement = Column(Integer)
    penalty = Column(Float, default=0.0)

    group = relationship("Group")
    show = relationship("Show")
    caption_scores = relationship("CaptionScore", back_populates="performance")

    @hybrid_property
    def averaged_caption_scores(self):
        """
        Returns a dict of {caption: weighted_average_score} for this performance,
        averaging across all judges and applying the season's caption weights.

        This is basically a really stupid way to get comp + perf / 2
        """
        # Collect each judge’s raw average for the caption
        temp = defaultdict(list)
        for cs in self.caption_scores:
            avg = (cs.comp_score + cs.perf_score) / 2
            temp[cs.caption].append(avg)

        # Build a map of caption → weight from the season
        weights = {w.caption: w.weight for w in self.show.season.caption_weights}

        # Compute weighted averages
        return {
            cap: round(
                sum(val * weights.get(cap, 0) / 100 for val in vals) / len(vals),
                3
            )
            for cap, vals in temp.items()
        }

# Individual caption-level score for a performance
class CaptionScore(Base):
    __tablename__ = "caption_scores"
    id = Column(Integer, primary_key=True)
    performance_id = Column(Integer, ForeignKey("performances.id"))
    caption = Column(String)
    weight = Column(Float)
    comp_score = Column(Float)
    perf_score = Column(Float)
    placement = Column(Integer)
    judge_id = Column(Integer, ForeignKey("judges.id"))

    performance = relationship("Performance", back_populates="caption_scores")
    judge = relationship("Judge", back_populates="caption_scores")
