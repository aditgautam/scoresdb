from sqlalchemy import Column, Integer, String, Float, ForeignKey, Date
from sqlalchemy.orm import relationship, declarative_base
from datetime import date

Base = declarative_base()


# Represents a competitive season, for example 2025
class Season(Base):
    __tablename__ = "seasons"
    id = Column(Integer, primary_key=True)
    year = Column(Integer, unique=True, nullable=False)
    shows = relationship("Show", back_populates="season")


# Host school / location, eg Monrovia HS or CSUF
class HostLocation(Base):
    __tablename__ = "hosts"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    city = Column(String)
    state = Column(String)
    shows = relationship("Show", back_populates="host")


# Regular season and championship shows
class Show(Base):
    __tablename__ = "shows"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)  # Distinguishes between Saturday and Sunday
    date = Column(Date, default=date.today)

    season_id = Column(Integer, ForeignKey("seasons.id"))
    host_id = Column(Integer, ForeignKey("hosts.id"))

    season = relationship("Season", back_populates="shows")
    host = relationship("HostLocation", back_populates="shows")
    judge_assignments = relationship("JudgeAssignment", back_populates="show")


# Caption assignment for a judge at a specific show
class JudgeAssignment(Base):
    __tablename__ = "judge_assignments"
    id = Column(Integer, primary_key=True)
    show_id = Column(Integer, ForeignKey("shows.id"))
    judge_id = Column(Integer, ForeignKey("judges.id"))
    caption = Column(String)  # e.g., "Music Effect", "Visual", "Music"

    show = relationship("Show", back_populates="judge_assignments")
    judge = relationship("Judge", back_populates="assignments")


# Judges — now just names and IDs
class Judge(Base):
    __tablename__ = "judges"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    # Assignments help us identify what show each judge adjucated
    assignments = relationship("JudgeAssignment", back_populates="judge")
    caption_scores = relationship("CaptionScore", back_populates="judge")


# Classification: PIW, PSW, PIO, etc
class Classification(Base):
    __tablename__ = "classifications"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    groups = relationship("Group", back_populates="classification")


# Each group
class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    home_city = Column(String)

    classification_id = Column(Integer, ForeignKey("classifications.id"))
    classification = relationship("Classification", back_populates="groups")


# Each unique scored performance
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


# Subcaption scores, includes relative placements
class CaptionScore(Base):
    __tablename__ = "caption_scores"
    id = Column(Integer, primary_key=True)
    performance_id = Column(Integer, ForeignKey("performances.id"))
    caption = Column(String)  # "Vis effect" "music effect" etc
    weight = Column(Float)

    comp_score = Column(Float)
    perf_score = Column(Float)
    placement = Column(Integer)

    judge_id = Column(Integer, ForeignKey("judges.id"))

    performance = relationship("Performance", back_populates="caption_scores")
    judge = relationship("Judge", back_populates="caption_scores")
